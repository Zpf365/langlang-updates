from __future__ import annotations

import json
from pathlib import Path

from weimi_manager.auth_config import AuthConfig
from weimi_manager.config import AppPaths


def test_auth_config_can_be_recovered_from_previous_onefolder_update(
    tmp_path: Path,
) -> None:
    program = tmp_path / "dist" / "LangLang"
    program.mkdir(parents=True)
    backup = tmp_path / "dist" / "LangLang.update-backup-20261110-010203"
    config_path = backup / "_internal" / "assets" / "auth_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "supabase_url": "https://audit.example.invalid",
                "publishable_key": "publishable-test-value",
            }
        ),
        encoding="utf-8",
    )
    data = tmp_path / "local" / "LangLang" / "data"
    paths = AppPaths(
        project_dir=program,
        resource_dir=program,
        data_dir=data,
        database=data / "weimi_manager.db",
    )

    config = AuthConfig.load(paths)

    assert config.configured
    assert config.supabase_url == "https://audit.example.invalid"
