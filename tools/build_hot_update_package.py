from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from weimi_manager.update_protocol import (  # noqa: E402
    SAFE_PATCH_ID,
    PatchFile,
    RuleBinding,
    parse_signed_channel,
    sign_manifest,
)
from weimi_manager.update_rules import validate_rule_payload  # noqa: E402

CHANNEL_ASSET_NAME = "hot_update_channel.json"
SAFE_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")
SAFE_RULE_NAME = re.compile(r"^[a-z][a-z0-9_]{2,80}$")


@dataclass(frozen=True, slots=True)
class RuleSource:
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class UpdatePackageResult:
    output_dir: Path
    channel_manifest: Path
    channel_sha256: str
    public_key: str
    webui_archive: Path
    webui_archive_sha256: str
    webui_files: tuple[PatchFile, ...]
    rules_archive: Path | None = None
    rules_archive_sha256: str = ""
    rules_files: tuple[PatchFile, ...] = ()
    rules_bindings: tuple[RuleBinding, ...] = ()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_https_url(value: str, label: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"{label}必须是无凭据的 HTTPS 地址")
    return text


def _safe_patch_id(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not SAFE_PATCH_ID.fullmatch(text):
        raise ValueError(f"{label} 无效")
    return text


def _collect_webui_files(source_dir: Path) -> list[tuple[str, bytes]]:
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise ValueError(f"WebUI 构建目录不存在：{source}")
    if source.is_symlink():
        raise ValueError("WebUI 构建目录禁止使用符号链接")
    collected: list[tuple[str, bytes]] = []
    for root, directories, filenames in os.walk(
        source,
        followlinks=False,
    ):
        root_path = Path(root)
        directories.sort()
        for directory in directories:
            candidate = root_path / directory
            if candidate.is_symlink():
                raise ValueError(f"WebUI 构建目录禁止符号链接：{candidate}")
        for filename in sorted(filenames):
            path = root_path / filename
            if path.is_symlink():
                raise ValueError(f"WebUI 构建产物禁止符号链接：{path}")
            relative = path.relative_to(source).as_posix()
            if not relative or "\\" in relative or ":" in relative:
                raise ValueError(f"WebUI 文件路径无效：{relative}")
            if path.suffix.casefold() not in {
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
            }:
                raise ValueError(f"WebUI 补丁文件类型不允许：{relative}")
            collected.append((relative, path.read_bytes()))
    collected.sort(key=lambda item: item[0])
    paths = {path for path, _content in collected}
    if "index.html" not in paths:
        raise ValueError("WebUI 构建产物缺少 index.html")
    return collected


def _collect_rule_files(
    rules: Sequence[RuleSource],
) -> tuple[list[tuple[str, bytes]], list[RuleBinding]]:
    collected: list[tuple[str, bytes]] = []
    bindings: list[RuleBinding] = []
    seen_names: set[str] = set()
    for source in sorted(rules, key=lambda item: item.name):
        name = str(source.name or "").strip()
        if not SAFE_RULE_NAME.fullmatch(name):
            raise ValueError(f"规则名称无效：{name or '空'}")
        if name in seen_names:
            raise ValueError(f"规则名称重复：{name}")
        seen_names.add(name)
        path = Path(source.path)
        if path.is_symlink():
            raise ValueError(f"规则文件禁止符号链接：{path}")
        if not path.is_file():
            raise ValueError(f"规则文件不存在：{path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"规则文件 JSON 无效：{path}") from exc
        validate_rule_payload(name, payload)
        archive_path = f"{name}.json"
        collected.append(
            (
                archive_path,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
        )
        bindings.append(RuleBinding(name=name, path=archive_path))
    collected.sort(key=lambda item: item[0])
    return collected, bindings


def _write_deterministic_archive(
    path: Path,
    files: Iterable[tuple[str, bytes]],
) -> tuple[Path, tuple[PatchFile, ...]]:
    ordered = sorted(files, key=lambda item: item[0])
    payload = bytearray()
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as handle:
        with zipfile.ZipFile(
            handle,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for relative, content in ordered:
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    content,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        handle.seek(0)
        payload.extend(handle.read())
    _write_atomic(path, bytes(payload))
    return (
        path,
        tuple(PatchFile(path=relative, sha256=_sha256(content)) for relative, content in ordered),
    )


def _load_private_key(
    path: Path,
    *,
    repo_root: Path,
    password: bytes | None,
) -> tuple[Ed25519PrivateKey, str]:
    private_path = Path(path).resolve()
    repository = Path(repo_root).resolve()
    try:
        private_path.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("热更新签名私钥必须位于仓库外")
    if not private_path.is_file():
        raise ValueError(f"热更新签名私钥不存在：{private_path}")
    private_key = serialization.load_pem_private_key(
        private_path.read_bytes(),
        password=password,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("更新签名私钥必须是 Ed25519")
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private_key, public_key


def _patch_payload(
    *,
    kind: str,
    patch_id: str,
    sequence: int,
    url: str,
    archive_sha256: str,
    app_min: str,
    app_max: str,
    backend_contract: int,
    rollout: int,
    files: Sequence[PatchFile],
    rules: Sequence[RuleBinding] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "patch_id": patch_id,
        "sequence": int(sequence),
        "url": url,
        "archive_sha256": archive_sha256,
        "app_min": app_min,
        "app_max": app_max,
        "backend_contract": int(backend_contract),
        "rollout": int(rollout),
        "files": [
            {
                "path": item.path,
                "sha256": item.sha256,
            }
            for item in files
        ],
    }
    if kind == "rules":
        payload["rules"] = [
            {
                "name": item.name,
                "path": item.path,
            }
            for item in rules
        ]
    return payload


def build_update_package(
    webui_dir: Path,
    output_dir: Path,
    *,
    private_key_path: Path,
    key_id: str,
    product_version: str,
    channel_sequence: int,
    webui_patch_id: str,
    webui_sequence: int,
    webui_url: str,
    app_min: str,
    app_max: str,
    backend_contract: int,
    rollout: int = 100,
    rules: Sequence[RuleSource] = (),
    rules_patch_id: str = "",
    rules_sequence: int = 0,
    rules_url: str = "",
    paused: bool = False,
    revoked_patch_ids: Sequence[str] = (),
    repo_root: Path | None = None,
    password: bytes | None = None,
) -> UpdatePackageResult:
    repository = (
        Path(repo_root).resolve()
        if repo_root is not None
        else REPOSITORY_ROOT
    )
    private_key, public_key = _load_private_key(
        private_key_path,
        repo_root=repository,
        password=password,
    )
    safe_key_id = str(key_id or "").strip()
    if not SAFE_KEY_ID.fullmatch(safe_key_id):
        raise ValueError("签名 key_id 无效")
    if not 0 <= int(rollout) <= 100:
        raise ValueError("rollout 必须在 0 到 100 之间")
    webui_url = _require_https_url(webui_url, "WebUI 包下载地址")
    webui_id = _safe_patch_id(webui_patch_id, "WebUI patch_id")
    if int(webui_sequence) <= 0:
        raise ValueError("WebUI patch sequence 必须大于 0")
    if int(channel_sequence) <= 0:
        raise ValueError("channel sequence 必须大于 0")
    if int(backend_contract) <= 0:
        raise ValueError("backend_contract 必须大于 0")

    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    webui_files_payload = _collect_webui_files(Path(webui_dir))
    webui_archive, webui_files = _write_deterministic_archive(
        target / f"{webui_id}.zip",
        webui_files_payload,
    )
    webui_archive_sha256 = _sha256(webui_archive.read_bytes())

    patches = [
        _patch_payload(
            kind="webui",
            patch_id=webui_id,
            sequence=int(webui_sequence),
            url=webui_url,
            archive_sha256=webui_archive_sha256,
            app_min=str(app_min),
            app_max=str(app_max),
            backend_contract=int(backend_contract),
            rollout=int(rollout),
            files=webui_files,
        )
    ]
    rules_archive: Path | None = None
    rules_archive_sha256 = ""
    rules_files: tuple[PatchFile, ...] = ()
    rules_bindings: tuple[RuleBinding, ...] = ()
    if rules:
        rules_id = _safe_patch_id(rules_patch_id, "规则 patch_id")
        if int(rules_sequence) <= 0:
            raise ValueError("规则 patch sequence 必须大于 0")
        rules_url = _require_https_url(rules_url, "规则包下载地址")
        rule_payload, binding_sequence = _collect_rule_files(rules)
        rules_archive, rules_files = _write_deterministic_archive(
            target / f"{rules_id}.zip",
            rule_payload,
        )
        rules_archive_sha256 = _sha256(rules_archive.read_bytes())
        rules_bindings = tuple(binding_sequence)
        patches.append(
            _patch_payload(
                kind="rules",
                patch_id=rules_id,
                sequence=int(rules_sequence),
                url=rules_url,
                archive_sha256=rules_archive_sha256,
                app_min=str(app_min),
                app_max=str(app_max),
                backend_contract=int(backend_contract),
                rollout=int(rollout),
                files=rules_files,
                rules=rules_bindings,
            )
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "channel",
        "channel": "stable",
        "sequence": int(channel_sequence),
        "product_version": str(product_version),
        "paused": bool(paused),
        "revoked_patch_ids": sorted(
            {
                _safe_patch_id(item, "撤销 patch_id")
                for item in revoked_patch_ids
                if str(item or "").strip()
            }
        ),
        "patches": patches,
    }
    manifest["signature"] = sign_manifest(
        manifest,
        private_key,
        safe_key_id,
    )
    parse_signed_channel(
        manifest,
        {safe_key_id: public_key},
        current_version=str(product_version),
        backend_contract=int(backend_contract),
    )
    channel_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )
    channel_path = target / CHANNEL_ASSET_NAME
    _write_atomic(channel_path, channel_bytes)
    return UpdatePackageResult(
        output_dir=target,
        channel_manifest=channel_path,
        channel_sha256=_sha256(channel_bytes),
        public_key=public_key,
        webui_archive=webui_archive,
        webui_archive_sha256=webui_archive_sha256,
        webui_files=webui_files,
        rules_archive=rules_archive,
        rules_archive_sha256=rules_archive_sha256,
        rules_files=rules_files,
        rules_bindings=rules_bindings,
    )


def load_rules_manifest(path: Path) -> tuple[RuleSource, ...]:
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("规则来源清单必须是 JSON 对象")
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError("规则来源清单缺少 rules 列表")
    rules: list[RuleSource] = []
    for item in raw_rules:
        if not isinstance(item, Mapping):
            raise ValueError("规则来源条目格式无效")
        name = str(item.get("name") or "").strip()
        relative = str(item.get("path") or "").strip()
        if not relative:
            raise ValueError("规则来源条目缺少 path")
        candidate = (source.parent / relative).resolve()
        try:
            candidate.relative_to(source.parent)
        except ValueError as exc:
            raise ValueError("规则来源路径不能越过规则清单所在目录") from exc
        rules.append(RuleSource(name=name, path=candidate))
    return tuple(rules)


def _result_payload(result: UpdatePackageResult) -> dict[str, Any]:
    return {
        "output_dir": str(result.output_dir),
        "channel_manifest": str(result.channel_manifest),
        "channel_sha256": result.channel_sha256,
        "public_key": result.public_key,
        "webui_archive": str(result.webui_archive),
        "webui_archive_sha256": result.webui_archive_sha256,
        "webui_files": [
            {"path": item.path, "sha256": item.sha256} for item in result.webui_files
        ],
        "rules_archive": str(result.rules_archive) if result.rules_archive else "",
        "rules_archive_sha256": result.rules_archive_sha256,
        "rules_files": [
            {"path": item.path, "sha256": item.sha256} for item in result.rules_files
        ],
        "rules_bindings": [
            {"name": item.name, "path": item.path} for item in result.rules_bindings
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从已构建 WebUI 和受控 JSON 规则生成签名热更新频道资产",
    )
    parser.add_argument("--webui-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--product-version", required=True)
    parser.add_argument("--channel-sequence", type=int, required=True)
    parser.add_argument("--webui-patch-id", required=True)
    parser.add_argument("--webui-sequence", type=int, required=True)
    parser.add_argument("--webui-url", required=True)
    parser.add_argument("--app-min", required=True)
    parser.add_argument("--app-max", required=True)
    parser.add_argument("--backend-contract", type=int, required=True)
    parser.add_argument("--rollout", type=int, default=100)
    parser.add_argument("--rules-manifest", type=Path)
    parser.add_argument("--rules-patch-id", default="")
    parser.add_argument("--rules-sequence", type=int, default=0)
    parser.add_argument("--rules-url", default="")
    parser.add_argument("--paused", action="store_true")
    parser.add_argument("--revoked-patch-id", action="append", default=[])
    parser.add_argument("--password-env", default="")
    args = parser.parse_args()

    rules = (
        load_rules_manifest(args.rules_manifest)
        if args.rules_manifest is not None
        else ()
    )
    password = (
        os.environ.get(args.password_env, "").encode("utf-8")
        if args.password_env
        else None
    )
    result = build_update_package(
        args.webui_dir,
        args.output_dir,
        private_key_path=args.private_key,
        key_id=args.key_id,
        product_version=args.product_version,
        channel_sequence=args.channel_sequence,
        webui_patch_id=args.webui_patch_id,
        webui_sequence=args.webui_sequence,
        webui_url=args.webui_url,
        app_min=args.app_min,
        app_max=args.app_max,
        backend_contract=args.backend_contract,
        rollout=args.rollout,
        rules=rules,
        rules_patch_id=args.rules_patch_id,
        rules_sequence=args.rules_sequence,
        rules_url=args.rules_url,
        paused=args.paused,
        revoked_patch_ids=args.revoked_patch_id,
        password=password,
    )
    print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
