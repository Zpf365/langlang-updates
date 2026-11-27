from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable, Mapping

from .update_protocol import (
    PatchDescriptor,
    SignedChannel,
    UpdateProtocolError,
)

RuleValidator = Callable[[object], None]


class InstallLockBusy(RuntimeError):
    """Raised when another update installer owns the install lock."""


class UpdateInstallLock(AbstractContextManager["UpdateInstallLock"]):
    def __init__(self, updates_root: Path) -> None:
        self.path = Path(updates_root).resolve() / "install.lock"
        self._handle: Any = None

    def __enter__(self) -> "UpdateInstallLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InstallLockBusy("另一个更新安装正在进行") from exc
        self._handle = handle
        return self

    def __exit__(self, *args: object) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class HotUpdateStore:
    def __init__(
        self,
        updates_root: Path,
        *,
        public_keys: Mapping[str, str],
        current_version: str,
        backend_contract: int,
        install_id: str,
        rule_validators: Mapping[str, RuleValidator],
        lock_root: Path | None = None,
    ) -> None:
        self.root = Path(updates_root).resolve()
        self.lock_root = Path(lock_root or self.root).resolve()
        self.public_keys = dict(public_keys)
        self.current_version = str(current_version)
        self.backend_contract = int(backend_contract)
        self.install_id = str(install_id)
        self.rule_validators = dict(rule_validators)
        self.state_path = self.root / "state.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 1)
        payload.setdefault("channel_sequence", 0)
        payload.setdefault("paused", False)
        payload.setdefault("max_sequence", {"webui": 0, "rules": 0})
        payload.setdefault("active", {})
        payload.setdefault("revoked_patch_ids", [])
        return payload

    def _write_state(self, payload: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".state.",
            suffix=".tmp",
            dir=self.state_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _root_for_relative(self, relative: str) -> Path:
        if not relative:
            raise UpdateProtocolError("活动补丁路径为空")
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise UpdateProtocolError("活动补丁路径越界") from exc
        return candidate

    def _active_record(self, kind: str) -> dict[str, Any]:
        active = self._state().get("active")
        if not isinstance(active, dict):
            return {}
        value = active.get(kind)
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _previous_allowed(
        record: Any,
        revoked_patch_ids: set[str],
    ) -> dict[str, Any] | None:
        current = record if isinstance(record, dict) else None
        visited: set[str] = set()
        while current:
            patch_id = str(current.get("patch_id") or "")
            if not patch_id or patch_id in visited:
                return None
            if patch_id not in revoked_patch_ids:
                return dict(current)
            visited.add(patch_id)
            previous = current.get("previous")
            current = previous if isinstance(previous, dict) else None
        return None

    def _sanitize_previous_chain(
        self,
        record: dict[str, Any],
        revoked_patch_ids: set[str],
    ) -> None:
        current = record
        visited: set[str] = set()
        while current:
            patch_id = str(current.get("patch_id") or "")
            if not patch_id or patch_id in visited:
                current.pop("previous", None)
                return
            visited.add(patch_id)
            previous = self._previous_allowed(
                current.get("previous"),
                revoked_patch_ids,
            )
            if previous is None:
                current.pop("previous", None)
                return
            current["previous"] = previous
            current = previous

    def active_patch_id(self, kind: str) -> str:
        return str(self._active_record(kind).get("patch_id") or "")

    def last_sequence(self, kind: str) -> int:
        state = self._state()
        max_sequence = state.get("max_sequence")
        if not isinstance(max_sequence, dict):
            return 0
        try:
            return int(max_sequence.get(kind) or 0)
        except (TypeError, ValueError):
            return 0

    def active_root(self, kind: str, fallback: Path) -> Path:
        active = self._active_record(kind)
        root = self._root_for_relative(str(active.get("root") or "")) if active else None
        if root is not None and root.is_dir():
            if kind != "webui" or (root / "index.html").is_file():
                return root
        return Path(fallback).resolve()

    def rule_payload(self, name: str) -> Any:
        active = self._active_record("rules")
        bindings = active.get("rules")
        relative = str(bindings.get(name) or "") if isinstance(bindings, dict) else ""
        if not relative:
            return None
        path = self._root_for_relative(f"rules/{self.active_patch_id('rules')}/{relative}")
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def apply_channel_control(self, channel: SignedChannel) -> bool:
        with UpdateInstallLock(self.lock_root):
            state = self._state()
            previous_sequence = int(state.get("channel_sequence") or 0)
            if channel.sequence < previous_sequence:
                raise UpdateProtocolError("更新频道 sequence 已回放")
            if channel.sequence == previous_sequence:
                return False
            state["channel_sequence"] = channel.sequence
            state["paused"] = channel.paused
            raw_revoked = state.get("revoked_patch_ids")
            revoked_ids = {
                str(value)
                for value in (raw_revoked if isinstance(raw_revoked, list) else [])
                if str(value)
            }
            revoked_ids.update(channel.revoked_patch_ids)
            state["revoked_patch_ids"] = sorted(revoked_ids)
            active = state.get("active")
            if not isinstance(active, dict):
                active = {}
            for kind, record in list(active.items()):
                if not isinstance(record, dict):
                    active.pop(kind, None)
                    continue
                record = self._previous_allowed(record, revoked_ids)
                if record is not None:
                    self._sanitize_previous_chain(record, revoked_ids)
                    active[kind] = record
                else:
                    active.pop(kind, None)
            state["active"] = active
            self._write_state(state)
            return True

    def install_patch(
        self,
        patch: PatchDescriptor,
        archive_path: Path,
    ) -> Path:
        archive = Path(archive_path).resolve()
        actual_archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_archive_sha != patch.archive_sha256:
            raise UpdateProtocolError("补丁 ZIP SHA256 校验失败")

        staging_parent = self.root / patch.kind
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".staging-{patch.patch_id}-",
                dir=staging_parent,
            )
        )
        try:
            self._extract_and_verify(patch, archive, staging)
            with UpdateInstallLock(self.lock_root):
                state = self._state()
                max_sequence = state.get("max_sequence")
                if not isinstance(max_sequence, dict):
                    max_sequence = {"webui": 0, "rules": 0}
                current_max = int(max_sequence.get(patch.kind) or 0)
                if patch.sequence <= current_max:
                    raise UpdateProtocolError("补丁 sequence 已回放")
                final_root = staging_parent / patch.patch_id
                if final_root.exists():
                    shutil.rmtree(final_root)
                self._activate_directory(staging, final_root)
                staging = Path()

                active = state.get("active")
                if not isinstance(active, dict):
                    active = {}
                previous = self._previous_allowed(
                    active.get(patch.kind),
                    {
                        str(value)
                        for value in (
                            state.get("revoked_patch_ids")
                            if isinstance(state.get("revoked_patch_ids"), list)
                            else []
                        )
                    },
                )
                rule_mapping = {binding.name: binding.path for binding in patch.rules}
                active[patch.kind] = {
                    "patch_id": patch.patch_id,
                    "sequence": patch.sequence,
                    "root": str(final_root.relative_to(self.root)),
                    "healthy": False,
                    "previous": previous,
                    "rules": rule_mapping,
                }
                max_sequence[patch.kind] = patch.sequence
                state["active"] = active
                state["max_sequence"] = max_sequence
                self._write_state(state)
                return final_root
        except Exception:
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    def _extract_and_verify(
        self,
        patch: PatchDescriptor,
        archive: Path,
        destination: Path,
    ) -> None:
        expected = {item.path: item.sha256 for item in patch.files}
        try:
            with zipfile.ZipFile(archive) as package:
                infos = [info for info in package.infolist() if not info.is_dir()]
                names = {info.filename for info in infos}
                if names != set(expected):
                    raise UpdateProtocolError("补丁 ZIP 文件清单与签名清单不一致")
                total_size = 0
                for info in infos:
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if (mode & 0o170000) == 0o120000:
                        raise UpdateProtocolError("补丁 ZIP 禁止符号链接")
                    total_size += int(info.file_size)
                    if total_size > 200 * 1024 * 1024:
                        raise UpdateProtocolError("补丁 ZIP 解压后超过大小上限")
                    content = package.read(info)
                    if hashlib.sha256(content).hexdigest() != expected[info.filename]:
                        raise UpdateProtocolError(f"补丁文件 SHA256 校验失败：{info.filename}")
                    target = (destination / info.filename).resolve()
                    try:
                        target.relative_to(destination)
                    except ValueError as exc:
                        raise UpdateProtocolError("补丁 ZIP 路径越界") from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
        except zipfile.BadZipFile as exc:
            raise UpdateProtocolError("补丁 ZIP 格式无效") from exc

        if patch.kind == "webui":
            if not (destination / "index.html").is_file():
                raise UpdateProtocolError("WebUI 补丁缺少 index.html")
            return
        for binding in patch.rules:
            validator = self.rule_validators.get(binding.name)
            if validator is None:
                raise UpdateProtocolError(f"未注册规则校验器：{binding.name}")
            try:
                payload = json.loads((destination / binding.path).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise UpdateProtocolError(f"规则文件 JSON 无效：{binding.path}") from exc
            try:
                validator(payload)
            except Exception as exc:
                raise UpdateProtocolError(f"规则业务校验失败：{binding.name}：{exc}") from exc

    @staticmethod
    def _activate_directory(staging: Path, final_root: Path) -> None:
        for attempt in range(20):
            try:
                os.replace(staging, final_root)
                return
            except PermissionError:
                if attempt >= 19:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def rollback(self, kind: str) -> bool:
        with UpdateInstallLock(self.lock_root):
            state = self._state()
            active = state.get("active")
            if not isinstance(active, dict):
                return False
            current = active.get(kind)
            if not isinstance(current, dict):
                return False
            revoked_values = state.get("revoked_patch_ids")
            previous = self._previous_allowed(
                current.get("previous"),
                {
                    str(value)
                    for value in (revoked_values if isinstance(revoked_values, list) else [])
                },
            )
            if previous is not None:
                active[kind] = previous
            else:
                active.pop(kind, None)
            state["active"] = active
            self._write_state(state)
            return True

    def mark_healthy(self, kind: str, healthy: bool) -> None:
        with UpdateInstallLock(self.lock_root):
            state = self._state()
            active = state.get("active")
            record = active.get(kind) if isinstance(active, dict) else None
            if not isinstance(record, dict):
                return
            record["healthy"] = bool(healthy)
            active[kind] = record
            state["active"] = active
            self._write_state(state)
