from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from weimi_manager.update_protocol import sign_manifest


def sign_manifest_file(
    manifest_path: Path,
    private_key_path: Path,
    key_id: str,
    *,
    output_path: Path | None = None,
    password: bytes | None = None,
) -> Path:
    source = Path(manifest_path).resolve()
    private_path = Path(private_key_path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("更新清单必须是 JSON 对象")
    private_key = serialization.load_pem_private_key(
        private_path.read_bytes(),
        password=password,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("更新签名私钥必须是 Ed25519")
    payload["signature"] = sign_manifest(payload, private_key, key_id)

    target = Path(output_path).resolve() if output_path else source
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用外部 Ed25519 私钥签名澜琅更新清单",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("private_key", type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--password-env",
        default="",
        help="从该环境变量读取私钥口令；私钥口令不得写入仓库",
    )
    args = parser.parse_args()
    password = os.environ.get(args.password_env, "").encode("utf-8") if args.password_env else None
    output = sign_manifest_file(
        args.manifest,
        args.private_key,
        args.key_id,
        output_path=args.output,
        password=password,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
