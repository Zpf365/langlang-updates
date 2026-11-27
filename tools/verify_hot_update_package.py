from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from weimi_manager.hot_update_store import HotUpdateStore  # noqa: E402
from weimi_manager.update_protocol import (  # noqa: E402
    UpdateProtocolError,
    load_trust_config,
    parse_signed_channel,
)
from weimi_manager.update_rules import rule_validators  # noqa: E402


def verify_update_assets(
    channel_manifest: Path,
    *,
    trust_root: Path,
    current_version: str,
    backend_contract: int,
    webui_archive: Path,
    rules_archive: Path | None = None,
) -> tuple[str, ...]:
    trust = load_trust_config(Path(trust_root))
    if not trust.public_keys:
        raise UpdateProtocolError("公钥配置中没有可用的更新签名公钥")
    try:
        payload = json.loads(
            Path(channel_manifest).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateProtocolError("更新频道清单 JSON 无效") from exc
    if not isinstance(payload, Mapping):
        raise UpdateProtocolError("更新频道清单必须是 JSON 对象")
    channel = parse_signed_channel(
        payload,
        trust.public_keys,
        current_version=str(current_version),
        backend_contract=int(backend_contract),
    )
    archives = {
        "webui": Path(webui_archive).resolve(),
        "rules": Path(rules_archive).resolve() if rules_archive else None,
    }
    kinds = [patch.kind for patch in channel.patches]
    if len(kinds) != len(set(kinds)):
        raise UpdateProtocolError("一个发布频道不能包含重复补丁类型")
    verified: list[str] = []
    with tempfile.TemporaryDirectory(prefix="langlang-hot-verify-") as root:
        store = HotUpdateStore(
            Path(root) / "hot",
            public_keys=trust.public_keys,
            current_version=str(current_version),
            backend_contract=int(backend_contract),
            install_id="verify-only",
            rule_validators=rule_validators,
        )
        for patch in channel.patches:
            archive = archives.get(patch.kind)
            if archive is None:
                raise UpdateProtocolError(
                    f"缺少 {patch.kind} 补丁 ZIP 路径"
                )
            store.stage_patch(patch, archive)
            verified.append(patch.patch_id)
    return tuple(verified)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用仓库公钥复核本地热更新频道资产",
    )
    parser.add_argument("--channel-manifest", type=Path, required=True)
    parser.add_argument("--trust-root", type=Path, required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--backend-contract", type=int, required=True)
    parser.add_argument("--webui-archive", type=Path, required=True)
    parser.add_argument("--rules-archive", type=Path)
    args = parser.parse_args()
    verified = verify_update_assets(
        args.channel_manifest,
        trust_root=args.trust_root,
        current_version=args.current_version,
        backend_contract=args.backend_contract,
        webui_archive=args.webui_archive,
        rules_archive=args.rules_archive,
    )
    print(
        json.dumps(
            {
                "verified": True,
                "patch_ids": list(verified),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
