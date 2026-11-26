"""Verify the final Windows package and its external update manifest."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__  # noqa: E402
from tools.generate_stable_release import (  # noqa: E402
    ASSET_NAME,
    SOURCE_EXCLUDED_DIRS,
    SOURCE_EXCLUDED_NAMES,
    SOURCE_EXCLUDED_SUFFIXES,
    verify_release_package,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify() -> dict[str, object]:
    release = ROOT / "release"
    package = release / ASSET_NAME
    source = release / f"LangLang_{__version__}_Source.zip"
    program = release / f"LangLang_{__version__}_Windows_x64"
    executable = program / "LangLang.exe"
    manifest_path = release / "update_manifest.json"
    root_manifest_path = ROOT / "update_manifest.json"
    internal_manifest = program / "_internal" / "update_manifest.json"
    root_package_manifest = program / "update_manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    root_manifest = json.loads(
        root_manifest_path.read_text(encoding="utf-8-sig")
    )
    if manifest != root_manifest:
        raise RuntimeError("根目录与 release 外置清单内容不一致")
    verify_release_package(package, manifest)

    bundle = json.loads(
        internal_manifest.read_text(encoding="utf-8-sig")
    )
    package_bundle = json.loads(
        root_package_manifest.read_text(encoding="utf-8-sig")
    )
    if bundle != package_bundle:
        raise RuntimeError("程序目录根清单与 _internal 清单内容不一致")
    if str(bundle.get("version") or "") != __version__:
        raise RuntimeError("程序目录包内清单版本不一致")
    if str(bundle.get("sha256") or ""):
        raise RuntimeError("程序目录包内清单 SHA256 必须为空")

    with zipfile.ZipFile(source) as archive:
        sensitive: list[str] = []
        for name in archive.namelist():
            path = PurePosixPath(name)
            if (
                path.name in SOURCE_EXCLUDED_NAMES
                or path.suffix.casefold() in SOURCE_EXCLUDED_SUFFIXES
                or any(
                    part in SOURCE_EXCLUDED_DIRS
                    for part in path.parts[:-1]
                )
            ):
                sensitive.append(name)
        if sensitive:
            raise RuntimeError(
                "源码包包含敏感或排除文件：" + ", ".join(sensitive)
            )
        source_file_count = len(archive.namelist())

    return {
        "version": __version__,
        "windows_zip": str(package),
        "windows_zip_sha256": sha256(package),
        "source_zip": str(source),
        "source_zip_sha256": sha256(source),
        "source_file_count": source_file_count,
        "exe": str(executable),
        "exe_sha256": sha256(executable),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "bundle_version": bundle["version"],
        "bundle_sha256": bundle["sha256"],
        "required_files": manifest["required_files"],
        "source_sensitive_files": [],
    }


def main() -> int:
    print(
        json.dumps(
            verify(),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
