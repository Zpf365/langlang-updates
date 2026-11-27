from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools import package_pyside


def test_candidate_manifest_replaces_stale_public_copy(
    tmp_path: Path,
) -> None:
    program = tmp_path / "LangLang"
    program.mkdir()
    (program / "LangLang.exe").write_bytes(b"test")
    stale = {
        "version": "1.9.00",
        "download_url": "https://example.invalid/old.zip",
        "sha256": "old",
    }
    internal = program / "_internal" / "update_manifest.json"
    internal.parent.mkdir()
    internal.write_text(json.dumps(stale), encoding="utf-8")

    manifest = {
        "version": "1.9.01",
        "download_url": "",
        "sha256": "",
        "notes": "## 发布\n\n- 新版本",
        "summary": [{"title": "发布", "items": ["新版本"]}],
    }
    package_pyside.write_program_manifests(program, manifest)
    archive_path = tmp_path / "candidate.zip"
    package_pyside.make_program_zip(
        program,
        archive_path,
        include_client_config=False,
        bundle_manifest=manifest,
    )

    assert json.loads(
        (program / "update_manifest.json").read_text(encoding="utf-8")
    ) == manifest
    assert json.loads(internal.read_text(encoding="utf-8")) == manifest
    with zipfile.ZipFile(archive_path) as archive:
        assert json.loads(
            archive.read("LangLang/update_manifest.json").decode("utf-8")
        ) == manifest
        assert json.loads(
            archive.read(
                "LangLang/_internal/update_manifest.json"
            ).decode("utf-8")
        ) == manifest


def test_release_notes_summary_uses_section_bullets() -> None:
    summary = package_pyside.summary_from_notes(
        "# 版本\n\n## 店铺\n\n- 第一项\n- 第二项\n\n## 上货\n\n- 第三项"
    )

    assert summary == [
        {"title": "店铺", "items": ["第一项", "第二项"]},
        {"title": "上货", "items": ["第三项"]},
    ]
