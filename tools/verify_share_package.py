"""Verify a portable share ZIP contains only public application files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__  # noqa: E402


FORBIDDEN_NAMES = {
    "saved_accounts.bin",
    "test-update-policy.json",
    "tester_accounts.json",
    "legacy_account_claim.txt",
}
EXPECTED_RUST_SHA256 = (
    "1d4332a447cab13dec8e3a533ad579e0e562b79e40822c931f33769758fc7156"
)
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_share(package: Path) -> dict[str, object]:
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        forbidden = []
        for name in names:
            path = PurePosixPath(name)
            relative = path.parts[1:] if path.parts[:1] == ("LangLang",) else path.parts
            if (
                path.name in FORBIDDEN_NAMES
                or path.suffix.casefold()
                in {".db", ".sqlite", ".sqlite3", ".jsonl"}
                or (relative and relative[0].casefold() in {
                    "accounts",
                    "data",
                    "localappdata",
                    "logs",
                })
            ):
                forbidden.append(name)
        if forbidden:
            raise RuntimeError(
                "分享包包含禁止文件：" + ", ".join(forbidden)
            )
        roots = {
            PurePosixPath(name).parts[0]
            for name in names
            if PurePosixPath(name).parts
        }
        if roots != {"LangLang"}:
            raise RuntimeError("分享包必须只有一个 LangLang 根目录")
        required = (
            "LangLang/LangLang.exe",
            "LangLang/_internal/webui/dist/index.html",
            "LangLang/_internal/update_manifest.json",
            "LangLang/_internal/assets/auth_config.json",
            "LangLang/_internal/cryptography/hazmat/bindings/_rust.pyd",
        )
        missing = [name for name in required if name not in names]
        if missing:
            raise RuntimeError(
                "分享包缺少必需文件：" + ", ".join(missing)
            )
        manifest = json.loads(
            archive.read("LangLang/_internal/update_manifest.json")
        )
        if str(manifest.get("version") or "") != __version__:
            raise RuntimeError("分享包版本不一致")
        if (
            str(manifest.get("channel") or "") != "stable"
            or manifest.get("prerelease") is not False
            or str(manifest.get("download_url") or "")
            or str(manifest.get("sha256") or "")
        ):
            raise RuntimeError("分享包更新清单不是独立分享模式")
        auth_config = json.loads(
            archive.read("LangLang/_internal/assets/auth_config.json")
        )
        if (
            not str(auth_config.get("supabase_url") or "").startswith(
                "https://"
            )
            or not str(
                auth_config.get("publishable_key")
                or auth_config.get("anon_key")
                or ""
            )
        ):
            raise RuntimeError("分享包账号服务配置不完整")
        rust_bytes = archive.read(
            "LangLang/_internal/cryptography/hazmat/bindings/_rust.pyd"
        )
        rust_sha256 = hashlib.sha256(rust_bytes).hexdigest()
        if rust_sha256 != EXPECTED_RUST_SHA256:
            raise RuntimeError("分享包 cryptography Rust 模块哈希不一致")
        return {
            "package": str(package),
            "sha256": _sha256(package),
            "file_count": len(names),
            "version": manifest["version"],
            "channel": manifest["channel"],
            "prerelease": manifest["prerelease"],
            "auth_config_present": True,
            "rust_module_sha256": rust_sha256,
            "forbidden_files": [],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            verify_share(args.package),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
