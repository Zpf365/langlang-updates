from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from .hot_update_store import HotUpdateStore
from .update_protocol import (
    PatchDescriptor,
    parse_signed_channel,
)

MAX_PATCH_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeCycle:
    staged_patch_ids: tuple[str, ...] = ()
    activated_patch_ids: tuple[str, ...] = ()
    reload_requested: bool = False

    @property
    def installed_patch_ids(self) -> tuple[str, ...]:
        return self.activated_patch_ids


def fetch_signed_channel(source: str) -> Mapping[str, Any]:
    response = requests.get(source, timeout=(8, 30))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("更新频道清单格式无效")
    return payload


def download_patch_archive(
    patch: PatchDescriptor,
    destination_dir: Path,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{patch.patch_id}.",
        suffix=".zip.tmp",
        dir=destination_dir,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with requests.get(
                patch.url,
                stream=True,
                timeout=(8, 60),
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_PATCH_BYTES:
                        raise ValueError("补丁下载超过大小上限")
                    handle.write(chunk)
                    digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if digest.hexdigest() != patch.archive_sha256:
            raise ValueError("补丁下载 SHA256 校验失败")
        target = destination_dir / f"{patch.patch_id}.zip"
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def rollout_bucket(install_id: str, patch_id: str) -> int:
    digest = hashlib.sha256(f"{install_id}\0{patch_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 100


def load_or_create_install_id(updates_root: Path) -> str:
    root = Path(updates_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "install-id"
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    if re.fullmatch(r"[0-9a-f]{32}", value):
        return value
    value = secrets.token_hex(16)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".install-id.",
        suffix=".tmp",
        dir=root,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


class HotUpdateRuntime:
    def __init__(
        self,
        store: HotUpdateStore,
        *,
        channel_loader: Callable[[], Mapping[str, Any]],
        archive_loader: Callable[[PatchDescriptor], Path] | None = None,
        busy_provider: Callable[[], bool],
        reload_handler: Callable[[], None] | None = None,
        log_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.store = store
        self.channel_loader = channel_loader
        self.archive_loader = archive_loader or (
            lambda patch: download_patch_archive(
                patch,
                store.root / "downloads",
            )
        )
        self.busy_provider = busy_provider
        self.reload_handler = reload_handler
        self.log_callback = log_callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._action_lock = threading.RLock()
        self._pending_channel: Any = None
        self._reload_pending = False

    def _log(self, event: str, value: str = "") -> None:
        if self.log_callback is not None:
            self.log_callback(event, value)

    def poll_once(self) -> RuntimeCycle:
        with self._action_lock:
            payload = self.channel_loader()
            channel = parse_signed_channel(
                payload,
                self.store.public_keys,
                current_version=self.store.current_version,
                backend_contract=self.store.backend_contract,
            )
            self.store.validate_channel_control(channel)
            staged: list[str] = []
            if not channel.paused:
                for patch in sorted(
                    channel.patches,
                    key=lambda item: item.sequence,
                ):
                    if patch.patch_id in channel.revoked_patch_ids:
                        continue
                    if self.store.last_sequence(patch.kind) >= patch.sequence:
                        continue
                    if (
                        rollout_bucket(
                            self.store.install_id,
                            patch.patch_id,
                        )
                        >= patch.rollout
                    ):
                        self._log(
                            "hotupdate.rollout_skipped",
                            patch.patch_id,
                        )
                        continue
                    archive = self.archive_loader(patch)
                    self.store.stage_patch(patch, archive)
                    staged.append(patch.patch_id)
                    self._log("hotupdate.staged", patch.patch_id)

            self._pending_channel = channel
            activated, reload_requested = self._process_pending_if_idle_locked()
            return RuntimeCycle(
                staged_patch_ids=tuple(staged),
                activated_patch_ids=tuple(activated),
                reload_requested=reload_requested,
            )

    def process_pending_if_idle(self) -> RuntimeCycle:
        with self._action_lock:
            activated, reload_requested = self._process_pending_if_idle_locked()
            return RuntimeCycle(
                activated_patch_ids=tuple(activated),
                reload_requested=reload_requested,
            )

    def has_pending_work(self) -> bool:
        with self._action_lock:
            return self._pending_channel is not None or self._reload_pending

    def _process_pending_if_idle_locked(
        self,
    ) -> tuple[list[str], bool]:
        try:
            busy = bool(self.busy_provider())
        except Exception:
            busy = True
        if busy:
            return [], False

        channel: Any = self._pending_channel
        if channel is None:
            return self._activate_staged_if_requested({}, set())

        self.store.validate_channel_control(channel)
        before_webui = self.store.active_patch_id("webui")
        self.store.apply_channel_control(channel)
        if self.store.active_patch_id("webui") != before_webui:
            self._reload_pending = True

        requested = {
            patch.kind: patch.patch_id
            for patch in channel.patches
            if patch.patch_id not in channel.revoked_patch_ids
        }
        self._pending_channel = None
        return self._activate_staged_if_requested(
            requested,
            set(channel.revoked_patch_ids),
        )

    def _activate_staged_if_requested(
        self,
        requested: Mapping[str, str],
        revoked_patch_ids: set[str],
    ) -> tuple[list[str], bool]:
        activated: list[str] = []
        webui_activated = False
        for kind in ("webui", "rules"):
            pending_patch_id = self.store.pending_patch_id(kind)
            if (
                not pending_patch_id
                or pending_patch_id in revoked_patch_ids
                or requested.get(kind) != pending_patch_id
            ):
                continue
            active_patch_id = self.store.activate_pending(kind)
            if active_patch_id:
                activated.append(active_patch_id)
                webui_activated = webui_activated or kind == "webui"
                self._log("hotupdate.activated", active_patch_id)

        if webui_activated:
            self._reload_pending = True
        reload_requested = self.request_reload_if_idle() if self._reload_pending else False
        return activated, reload_requested

    def request_reload_if_idle(self) -> bool:
        try:
            busy = bool(self.busy_provider())
        except Exception:
            busy = True
        if busy or self.reload_handler is None:
            self._log("hotupdate.reload_deferred")
            return False
        self.reload_handler()
        self._reload_pending = False
        return True

    def mark_webui_ready(self, healthy: bool) -> bool:
        if healthy:
            self.store.mark_healthy("webui", True)
            self._log("hotupdate.healthy", self.store.active_patch_id("webui"))
            return True
        self._log("hotupdate.unhealthy", self.store.active_patch_id("webui"))
        rolled_back = self.store.rollback("webui")
        if rolled_back:
            self._log("hotupdate.rolled_back", self.store.active_patch_id("webui"))
        return rolled_back

    def start(self, *, interval_seconds: float = 6 * 60 * 60) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def worker() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception as exc:
                    self._log("hotupdate.failed", str(exc))
                if self._stop.wait(max(30.0, float(interval_seconds))):
                    break

        self._thread = threading.Thread(
            target=worker,
            name="langlang-hot-update",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
