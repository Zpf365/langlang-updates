from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


APP_NAME = "WeimiManager"


def _database_store_count(database: Path) -> int:
    if not database.is_file():
        return 0
    source = None
    try:
        source = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        row = source.execute("SELECT COUNT(*) FROM stores").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        if source is not None:
            source.close()


def _copy_missing_assets(legacy: Path, target: Path) -> None:
    """Copy login/profile assets without overwriting newer target files."""
    target.mkdir(parents=True, exist_ok=True)
    disposable_parts = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnCache",
        "GraphiteDawnCache",
        "GrShaderCache",
        "ShaderCache",
        "Crashpad",
        "BrowserMetrics",
    }
    for directory_name in ("edge_profiles", "web_sessions", "backups"):
        source_directory = legacy / directory_name
        if not source_directory.is_dir():
            continue
        destination_directory = target / directory_name
        for source_file in source_directory.rglob("*"):
            if not source_file.is_file():
                continue
            relative = source_file.relative_to(source_directory)
            if any(part in disposable_parts for part in relative.parts):
                continue
            destination = destination_directory / relative
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source_file, destination)
            except OSError:
                continue
    for file_name in ("web_login.log",):
        source_file = legacy / file_name
        destination = target / file_name
        if source_file.is_file() and not destination.exists():
            try:
                shutil.copy2(source_file, destination)
            except OSError:
                pass


def _backup_empty_target(target_database: Path, target: Path) -> None:
    if not target_database.is_file():
        return
    backup_directory = target / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_directory / f"pre-migration-{stamp}.db"
    try:
        shutil.copy2(target_database, backup)
    except OSError:
        pass


def _migrate_legacy_database(legacy: Path, target: Path) -> bool:
    legacy_database = legacy / "weimi_manager.db"
    target_database = target / "weimi_manager.db"
    legacy_count = _database_store_count(legacy_database)
    target_count = _database_store_count(target_database)
    if legacy_count <= 0 or target_count > 0:
        return False
    if legacy_database.resolve() == target_database.resolve():
        return False
    target.mkdir(parents=True, exist_ok=True)
    source = None
    destination = None
    try:
        _backup_empty_target(target_database, target)
        source = sqlite3.connect(
            f"{legacy_database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=15,
        )
        destination = sqlite3.connect(target_database, timeout=15)
        source.backup(destination)
        destination.close()
        destination = None
        _copy_missing_assets(legacy, target)
        with (target / "migration.log").open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"migrated stores={legacy_count} from={legacy}\n"
            )
        return True
    except (OSError, sqlite3.Error):
        # Keep both source and a possible empty target untouched. The account
        # centre can still restore the database manually.
        return False
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _legacy_data_directories(
    project_dir: Path,
    local_app_data: Path,
    roaming_app_data: Path,
) -> list[Path]:
    candidates = [
        project_dir / "data",
        project_dir.parent / "data",
        local_app_data / "LangLang" / "data",
        local_app_data / "LangLang",
        local_app_data / "WeimiManager" / "data",
        local_app_data / "WeimiManager",
        roaming_app_data / "LangLang" / "data",
        roaming_app_data / "WeimiManager" / "data",
    ]
    search_roots = [
        project_dir.parent,
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "Documents",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children[:250]:
            if child.is_dir():
                candidates.append(child / "data")
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(candidate)
    return result


def migrate_existing_user_data(
    project_dir: Path,
    data_dir: Path,
) -> Path | None:
    """Find and restore the most complete legacy store database.

    This runs only while the fixed user-data directory has no stores. It
    covers old portable packages where ``data`` lived beside the executable,
    renamed distribution folders, and earlier local-app-data locations.
    """
    target_database = data_dir / "weimi_manager.db"
    if _database_store_count(target_database) > 0:
        return None
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    )
    roaming_app_data = Path(
        os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    )
    candidates = []
    for legacy in _legacy_data_directories(
        project_dir,
        local_app_data,
        roaming_app_data,
    ):
        legacy_database = legacy / "weimi_manager.db"
        if legacy_database.resolve() == target_database.resolve():
            continue
        count = _database_store_count(legacy_database)
        if count <= 0:
            continue
        try:
            modified = legacy_database.stat().st_mtime
        except OSError:
            modified = 0
        candidates.append((count, modified, legacy))
    for _count, _modified, legacy in sorted(
        candidates,
        key=lambda item: (item[0], item[1]),
        reverse=True,
    ):
        if _migrate_legacy_database(legacy, data_dir):
            return legacy
    return None


def account_data_directory(base_data_dir: Path, account_id: str) -> Path:
    """Return an isolated data directory without exposing the raw account ID."""
    import hashlib

    safe_id = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:24]
    return base_data_dir.parent / "accounts" / safe_id / "data"


def migrate_legacy_data_to_account(
    base_data_dir: Path,
    account_id: str,
) -> Path:
    """Give the first signed-in account the pre-login data exactly once.

    The legacy directory is copied, never deleted. Later accounts start with
    an empty directory, while updates continue to use the same account path.
    """
    target = account_data_directory(base_data_dir, account_id)
    target.mkdir(parents=True, exist_ok=True)
    target_database = target / "weimi_manager.db"
    if _database_store_count(target_database) > 0:
        return target

    claim_path = base_data_dir.parent / "legacy_account_claim.txt"
    safe_id = re.sub(r"[^0-9a-f]", "", target.parent.name.lower())
    try:
        claimed_by = claim_path.read_text(encoding="utf-8").strip()
    except OSError:
        claimed_by = ""
    if claimed_by and claimed_by != safe_id:
        return target

    legacy_database = base_data_dir / "weimi_manager.db"
    if _database_store_count(legacy_database) > 0:
        migrated = _migrate_legacy_database(base_data_dir, target)
        if migrated:
            claim_path.write_text(safe_id + "\n", encoding="utf-8")
    return target


@dataclass(frozen=True)
class AppPaths:
    project_dir: Path
    resource_dir: Path
    data_dir: Path
    database: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        source_root = Path(__file__).resolve().parent.parent
        frozen = bool(getattr(sys, "frozen", False))
        project_dir = Path(sys.executable).resolve().parent if frozen else source_root
        resource_dir = Path(getattr(sys, "_MEIPASS", source_root))
        override = os.environ.get("WEIMI_DATA_DIR")
        if override:
            data_dir = Path(override).expanduser().resolve()
        elif frozen:
            local_app_data = Path(
                os.environ.get("LOCALAPPDATA")
                or Path.home() / "AppData" / "Local"
            )
            data_dir = local_app_data / "LangLang" / "data"
            migrate_existing_user_data(project_dir, data_dir)
        else:
            data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            project_dir=project_dir,
            resource_dir=resource_dir,
            data_dir=data_dir,
            database=data_dir / "weimi_manager.db",
        )

    def for_account(self, account_id: str) -> "AppPaths":
        data_dir = migrate_legacy_data_to_account(
            self.data_dir,
            account_id,
        )
        return AppPaths(
            project_dir=self.project_dir,
            resource_dir=self.resource_dir,
            data_dir=data_dir,
            database=data_dir / "weimi_manager.db",
        )

