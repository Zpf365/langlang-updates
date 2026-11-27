from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SAFE_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")


@dataclass(frozen=True, slots=True)
class SigningKeyInfo:
    key_id: str
    public_key: str
    fingerprint: str


def create_signing_key(
    private_key_path: Path,
    *,
    key_id: str,
    repo_root: Path,
) -> SigningKeyInfo:
    repository = Path(repo_root).resolve()
    private_path = Path(private_key_path).resolve()
    try:
        private_path.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("热更新签名私钥必须位于仓库外")
    safe_key_id = str(key_id or "").strip()
    if not SAFE_KEY_ID.fullmatch(safe_key_id):
        raise ValueError("签名 key_id 无效")

    private_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(private_path.parent, 0o700)
    except OSError:
        pass
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    descriptor = os.open(
        private_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(private_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    except Exception:
        private_path.unlink(missing_ok=True)
        raise

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return SigningKeyInfo(
        key_id=safe_key_id,
        public_key=base64.b64encode(public_raw).decode("ascii"),
        fingerprint=hashlib.sha256(public_raw).hexdigest(),
    )


def trust_config_payload(
    info: SigningKeyInfo,
    *,
    enabled: bool = False,
    channel_url: str = "",
    blocker: str = "受控 HTTPS 补丁频道尚未配置",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "enabled": bool(enabled),
        "blocker": str(blocker),
        "channel_url": str(channel_url),
        "keys": [
            {
                "key_id": info.key_id,
                "public_key": info.public_key,
            }
        ],
    }


def write_public_trust_config(
    path: Path,
    info: SigningKeyInfo,
    *,
    enabled: bool = False,
    channel_url: str = "",
    blocker: str = "受控 HTTPS 补丁频道尚未配置",
) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = trust_config_payload(
        info,
        enabled=enabled,
        channel_url=channel_url,
        blocker=blocker,
    )
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在仓库外创建独立 Ed25519 热更新发布密钥",
    )
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--public-config", type=Path)
    parser.add_argument("--channel-url", default="")
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument(
        "--blocker",
        default="受控 HTTPS 补丁频道尚未配置",
    )
    args = parser.parse_args()

    info = create_signing_key(
        args.private_key,
        key_id=args.key_id,
        repo_root=args.repo_root,
    )
    config_path = ""
    if args.public_config is not None:
        config_path = str(
            write_public_trust_config(
                args.public_config,
                info,
                enabled=args.enabled,
                channel_url=args.channel_url,
                blocker=args.blocker,
            )
        )
    print(
        json.dumps(
            {
                "key_id": info.key_id,
                "fingerprint": info.fingerprint,
                "public_key": info.public_key,
                "public_config": config_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
