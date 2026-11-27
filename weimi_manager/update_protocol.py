from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

TRUST_CONFIG_RELATIVE = Path("assets") / "update_public_keys.json"
SAFE_PATCH_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,80}$")
SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2,3}$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WEBUI_ALLOWED_SUFFIXES = {
    ".css",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".png",
    ".svg",
    ".ttf",
    ".woff",
    ".woff2",
}
RULES_ALLOWED_SUFFIXES = {".json"}


class UpdateProtocolError(ValueError):
    """Raised when an update manifest is untrusted or incompatible."""


@dataclass(frozen=True, slots=True)
class TrustConfig:
    enabled: bool
    public_keys: Mapping[str, str]
    blocker: str
    channel_url: str = ""


@dataclass(frozen=True, slots=True)
class PatchFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RuleBinding:
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class PatchDescriptor:
    kind: str
    patch_id: str
    sequence: int
    url: str
    archive_sha256: str
    app_min: str
    app_max: str
    backend_contract: int
    rollout: int
    files: tuple[PatchFile, ...]
    rules: tuple[RuleBinding, ...]


@dataclass(frozen=True, slots=True)
class SignedChannel:
    schema_version: int
    channel: str
    sequence: int
    product_version: str
    paused: bool
    revoked_patch_ids: frozenset[str]
    patches: tuple[PatchDescriptor, ...]


def _without_signature(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if key != "signature"}


def canonical_manifest(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _without_signature(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_manifest(
    payload: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    key_id: str,
) -> dict[str, str]:
    signature = private_key.sign(canonical_manifest(payload))
    return {
        "algorithm": "ed25519",
        "key_id": str(key_id),
        "value": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest_signature(
    payload: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> str:
    signature = payload.get("signature")
    if not isinstance(signature, Mapping):
        raise UpdateProtocolError("更新清单缺少签名")
    if str(signature.get("algorithm") or "").casefold() != "ed25519":
        raise UpdateProtocolError("更新清单签名算法不受支持")
    key_id = str(signature.get("key_id") or "")
    encoded_public_key = public_keys.get(key_id)
    if not encoded_public_key:
        raise UpdateProtocolError("更新清单签名密钥未知")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(encoded_public_key, validate=True)
        )
        signature_bytes = base64.b64decode(
            str(signature.get("value") or ""),
            validate=True,
        )
        public_key.verify(signature_bytes, canonical_manifest(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise UpdateProtocolError("更新清单签名校验失败") from exc
    return key_id


def load_trust_config(resource_dir: Path) -> TrustConfig:
    path = Path(resource_dir) / TRUST_CONFIG_RELATIVE
    if not path.is_file():
        return TrustConfig(False, {}, "正式更新公钥配置不存在")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return TrustConfig(False, {}, f"正式更新公钥配置无效：{exc}")
    if not isinstance(payload, Mapping):
        return TrustConfig(False, {}, "正式更新公钥配置格式无效")
    keys: dict[str, str] = {}
    raw_keys = payload.get("keys")
    if isinstance(raw_keys, list):
        for item in raw_keys:
            if not isinstance(item, Mapping):
                continue
            key_id = str(item.get("key_id") or "").strip()
            value = str(item.get("public_key") or "").strip()
            if key_id and value:
                keys[key_id] = value
    enabled = payload.get("enabled") is True and bool(keys)
    blocker = str(payload.get("blocker") or "").strip()
    channel_url = str(payload.get("channel_url") or "").strip()
    if channel_url and not channel_url.startswith("https://"):
        channel_url = ""
        blocker = blocker or "受控补丁频道必须使用 HTTPS"
    if not enabled and not blocker:
        blocker = "正式签名私钥或受控发布频道尚未建立"
    return TrustConfig(enabled, keys, blocker, channel_url)


def version_tuple(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not SAFE_VERSION.fullmatch(text):
        raise UpdateProtocolError(f"版本号无效：{text or '空'}")
    return tuple(int(part) for part in text.split("."))


def _version_in_range(value: str, minimum: str, maximum: str) -> bool:
    current = version_tuple(value)
    lower = version_tuple(minimum)
    upper = version_tuple(maximum)
    width = max(len(current), len(lower), len(upper))

    def pad(item: tuple[int, ...]) -> tuple[int, ...]:
        return item + (0,) * (width - len(item))

    return pad(lower) <= pad(current) <= pad(upper)


def _validate_sha256(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if not SAFE_SHA256.fullmatch(digest):
        raise UpdateProtocolError(f"{label} SHA256 无效")
    return digest


def _validate_patch_path(path: Any, *, kind: str) -> str:
    text = str(path or "").strip()
    if not text or "\\" in text or ":" in text or "\x00" in text:
        raise UpdateProtocolError(f"补丁文件路径无效：{text or '空'}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UpdateProtocolError(f"补丁文件路径无效：{text}")
    if re.match(r"^[A-Za-z]:", text):
        raise UpdateProtocolError(f"补丁文件路径无效：{text}")
    for part in pure.parts:
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES or part.endswith((" ", ".")):
            raise UpdateProtocolError(f"补丁文件路径无效：{text}")
    allowed = WEBUI_ALLOWED_SUFFIXES if kind == "webui" else RULES_ALLOWED_SUFFIXES
    if pure.suffix.casefold() not in allowed:
        raise UpdateProtocolError(f"补丁文件类型不允许：{text}")
    return pure.as_posix()


def _parse_patch(
    payload: Any,
    *,
    current_version: str,
    backend_contract: int,
) -> PatchDescriptor:
    if not isinstance(payload, Mapping):
        raise UpdateProtocolError("补丁描述格式无效")
    kind = str(payload.get("kind") or "").strip().casefold()
    if kind not in {"webui", "rules"}:
        raise UpdateProtocolError("补丁类型不受支持")
    patch_id = str(payload.get("patch_id") or "").strip()
    if not SAFE_PATCH_ID.fullmatch(patch_id):
        raise UpdateProtocolError("patch_id 无效")
    try:
        sequence = int(payload.get("sequence") or 0)
        backend_value = int(payload.get("backend_contract") or 0)
        rollout = int(payload.get("rollout", 100))
    except (TypeError, ValueError) as exc:
        raise UpdateProtocolError("补丁序号或兼容范围无效") from exc
    if sequence <= 0:
        raise UpdateProtocolError("补丁 sequence 必须大于 0")
    if backend_value != int(backend_contract):
        raise UpdateProtocolError("补丁与当前后端契约不兼容")
    if not 0 <= rollout <= 100:
        raise UpdateProtocolError("补丁 rollout 必须在 0 到 100 之间")
    app_min = str(payload.get("app_min") or "").strip()
    app_max = str(payload.get("app_max") or "").strip()
    if not _version_in_range(current_version, app_min, app_max):
        raise UpdateProtocolError("补丁与当前应用版本不兼容")
    url = str(payload.get("url") or "").strip()
    if not url.startswith("https://"):
        raise UpdateProtocolError("补丁下载地址必须使用 HTTPS")
    archive_sha256 = _validate_sha256(
        payload.get("archive_sha256"),
        "补丁 ZIP",
    )
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise UpdateProtocolError("补丁文件清单为空")
    files: list[PatchFile] = []
    seen_paths: set[str] = set()
    for item in raw_files:
        if not isinstance(item, Mapping):
            raise UpdateProtocolError("补丁文件条目格式无效")
        path = _validate_patch_path(item.get("path"), kind=kind)
        if path in seen_paths:
            raise UpdateProtocolError(f"补丁文件重复：{path}")
        seen_paths.add(path)
        files.append(
            PatchFile(
                path=path,
                sha256=_validate_sha256(item.get("sha256"), path),
            )
        )
    if kind == "webui" and "index.html" not in seen_paths:
        raise UpdateProtocolError("WebUI 补丁缺少 index.html")

    rules: list[RuleBinding] = []
    raw_rules = payload.get("rules")
    if kind == "rules":
        if not isinstance(raw_rules, list) or not raw_rules:
            raise UpdateProtocolError("规则补丁缺少规则绑定")
        bound_paths: set[str] = set()
        for item in raw_rules:
            if not isinstance(item, Mapping):
                raise UpdateProtocolError("规则绑定格式无效")
            name = str(item.get("name") or "").strip()
            path = _validate_patch_path(item.get("path"), kind=kind)
            if not name or path not in seen_paths or path in bound_paths:
                raise UpdateProtocolError("规则绑定无效或重复")
            bound_paths.add(path)
            rules.append(RuleBinding(name=name, path=path))
        if bound_paths != seen_paths:
            raise UpdateProtocolError("规则补丁包含未绑定文件")
    elif raw_rules not in (None, []):
        raise UpdateProtocolError("WebUI 补丁不能包含规则绑定")

    return PatchDescriptor(
        kind=kind,
        patch_id=patch_id,
        sequence=sequence,
        url=url,
        archive_sha256=archive_sha256,
        app_min=app_min,
        app_max=app_max,
        backend_contract=backend_value,
        rollout=rollout,
        files=tuple(files),
        rules=tuple(rules),
    )


def parse_signed_channel(
    payload: Mapping[str, Any],
    public_keys: Mapping[str, str],
    *,
    current_version: str,
    backend_contract: int,
) -> SignedChannel:
    if not isinstance(payload, Mapping):
        raise UpdateProtocolError("更新频道清单格式无效")
    verify_manifest_signature(payload, public_keys)
    if int(payload.get("schema_version") or 0) != 1:
        raise UpdateProtocolError("更新频道 schema_version 不受支持")
    if str(payload.get("kind") or "").casefold() != "channel":
        raise UpdateProtocolError("更新频道 kind 无效")
    if str(payload.get("channel") or "").casefold() != "stable":
        raise UpdateProtocolError("更新频道必须为 stable")
    try:
        sequence = int(payload.get("sequence") or 0)
    except (TypeError, ValueError) as exc:
        raise UpdateProtocolError("更新频道 sequence 无效") from exc
    if sequence <= 0:
        raise UpdateProtocolError("更新频道 sequence 必须大于 0")
    product_version = str(payload.get("product_version") or "").strip()
    if len(version_tuple(product_version)) != 3:
        raise UpdateProtocolError("产品版本必须为三段")
    revoked = payload.get("revoked_patch_ids")
    if revoked is None:
        revoked = []
    if not isinstance(revoked, list):
        raise UpdateProtocolError("revoked_patch_ids 格式无效")
    revoked_ids = frozenset(str(value).strip() for value in revoked if str(value).strip())
    raw_patches = payload.get("patches")
    if not isinstance(raw_patches, list):
        raise UpdateProtocolError("patches 格式无效")
    patches: list[PatchDescriptor] = []
    seen_ids: set[str] = set()
    for item in raw_patches:
        patch = _parse_patch(
            item,
            current_version=current_version,
            backend_contract=backend_contract,
        )
        if patch.patch_id in seen_ids:
            raise UpdateProtocolError(f"patch_id 重复：{patch.patch_id}")
        seen_ids.add(patch.patch_id)
        patches.append(patch)
    return SignedChannel(
        schema_version=1,
        channel="stable",
        sequence=sequence,
        product_version=product_version,
        paused=bool(payload.get("paused", False)),
        revoked_patch_ids=revoked_ids,
        patches=tuple(patches),
    )
