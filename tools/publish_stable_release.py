"""Publish the generated stable package and manifest to GitHub."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager import __version__


REPOSITORY = "Zpf365/langlang-updates"
TAG = f"v{__version__}"
RELEASE = ROOT / "release"


def main() -> int:
    package = RELEASE / f"LangLang-{__version__}-win64.zip"
    source = RELEASE / f"LangLang_{__version__}_Source.zip"
    manifest = RELEASE / "update_manifest.json"
    for path in (package, source, manifest):
        if not path.is_file():
            raise FileNotFoundError(path)
    view = subprocess.run(
        ["gh", "release", "view", TAG, "--repo", REPOSITORY],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if view.returncode != 0:
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                TAG,
                "--repo",
                REPOSITORY,
                "--title",
                f"澜琅 {__version__}",
                "--notes-file",
                str(ROOT / f"RELEASE_NOTES_{__version__}.md"),
                "--latest",
            ],
            cwd=ROOT,
            check=True,
        )
    subprocess.run(
        [
            "gh",
            "release",
            "upload",
            TAG,
            "--repo",
            REPOSITORY,
            str(package),
            str(source),
            str(manifest),
            "--clobber",
        ],
        cwd=ROOT,
        check=True,
    )
    print(json.dumps(json.loads(manifest.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
