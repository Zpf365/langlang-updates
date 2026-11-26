"""Verify the upgraded test installation without exposing account credentials."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TARGET = Path(r"D:\WG\LangLang")
LOCAL_APP_DATA = Path(
    os.environ.get("LOCALAPPDATA")
    or (Path.home() / "AppData" / "Local")
)
LANG_DATA = LOCAL_APP_DATA / "LangLang"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    from weimi_manager.auth_config import AuthConfig
    from weimi_manager.auth_service import build_auth_service
    from weimi_manager.config import AppPaths

    data_dir = LANG_DATA / "data"
    paths = AppPaths(
        project_dir=ROOT,
        resource_dir=TARGET / "_internal",
        data_dir=data_dir,
        database=data_dir / "weimi_manager.db",
    )
    service = build_auth_service(
        AuthConfig.load(paths),
        LANG_DATA / "auth",
    )
    if service is None:
        raise RuntimeError("AuthService unavailable")
    accounts = service.store.list_accounts()
    if not accounts:
        raise RuntimeError("No saved account is available")
    saved = service.store.find("zpfly") or accounts[0]
    context = service.restore_account(saved)
    account_paths = paths.for_account(context.account_id)
    database_path = account_paths.database
    with sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        store_count = int(
            connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        )
        order_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='orders'"
        ).fetchone()[0]
        order_count = (
            int(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
            if order_table
            else 0
        )
    manifest = json.loads(
        (TARGET / "_internal" / "update_manifest.json").read_text(
            encoding="utf-8-sig"
        )
    )
    assets = TARGET / "_internal" / "webui" / "dist" / "assets"
    required_pages = {
        name: bool(list(assets.glob(f"{name}-*.js")))
        for name in (
            "StoresPage",
            "OrdersParityPage",
            "ProductsPage",
            "ActivityPage",
        )
    }
    state_files = sorted(
        (LANG_DATA / "updates" / "auto").glob("*/state.json")
    )
    state = (
        json.loads(state_files[-1].read_text(encoding="utf-8"))
        if state_files
        else {}
    )
    evidence = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "install_path": str(TARGET),
        "version": manifest.get("version"),
        "channel": manifest.get("channel"),
        "prerelease": manifest.get("prerelease"),
        "exe_sha256": sha256(TARGET / "LangLang.exe"),
        "webui_index": (TARGET / "_internal" / "webui" / "dist" / "index.html").is_file(),
        "key_pages": required_pages,
        "saved_account_count": len(accounts),
        "auth_restore_ok": bool(context.tokens.access_token),
        "auth_offline": bool(context.offline),
        "store_count": store_count,
        "order_count": order_count,
        "auto_update_state": state,
        "logs_present": {
            name: (TARGET / "logs" / "2026-11-23" / name).is_file()
            for name in (
                "app.log",
                "network.jsonl",
                "errors.log",
                "webui-events.jsonl",
            )
        },
    }
    target = ROOT / "deliveries" / "1.8.82" / "installed-update-evidence.json"
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
