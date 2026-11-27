from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class TaskPauseError(ValueError):
    """Raised when a new controlled operation is requested while paused."""

    code = "task_paused"

    def __init__(self, message: str = "全局操作已暂停，未启动新的平台任务") -> None:
        super().__init__(message)


class TaskPauseStateError(ValueError):
    """Raised when the persisted application pause state cannot be trusted."""

    code = "task_pause_state_error"


@dataclass(frozen=True)
class GlobalTaskPauseState:
    schema: int = 1
    scope: str = "application"
    paused: bool = False
    revision: int = 0
    changed_at: str = ""
    owner_account_id: str = ""
    owner_generation: int = 0
    reason: str = ""


class GlobalTaskPauseGate:
    """Persist one application-wide pause switch independent of account data."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._condition = threading.Condition(threading.RLock())
        self._state = GlobalTaskPauseState()
        self._error = ""
        self._reload()

    def _default_state(self) -> GlobalTaskPauseState:
        return GlobalTaskPauseState()

    def _reload(self) -> None:
        with self._condition:
            try:
                if not self.path.is_file():
                    self._state = self._default_state()
                    self._error = ""
                    return
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("暂停状态必须是 JSON 对象")
                paused = payload.get("paused")
                if not isinstance(paused, bool):
                    raise ValueError("暂停状态缺少布尔 paused")
                state = GlobalTaskPauseState(
                    schema=int(payload.get("schema") or 1),
                    scope=str(payload.get("scope") or "application"),
                    paused=paused,
                    revision=max(0, int(payload.get("revision") or 0)),
                    changed_at=str(payload.get("changed_at") or ""),
                    owner_account_id=str(
                        payload.get("owner_account_id") or ""
                    ),
                    owner_generation=max(
                        0,
                        int(payload.get("owner_generation") or 0),
                    ),
                    reason=str(payload.get("reason") or ""),
                )
                if state.scope != "application":
                    raise ValueError("暂停状态作用域不是 application")
            except Exception as exc:
                self._error = (
                    f"读取全局任务暂停状态失败：{type(exc).__name__}: {exc}"
                )
                return
            self._state = state
            self._error = ""

    def _persist_locked(self, state: GlobalTaskPauseState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        payload = json.dumps(
            asdict(state),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        self._state = state
        self._error = ""

    def snapshot(self, *, changed: bool | None = None) -> dict[str, Any]:
        with self._condition:
            self._reload()
            if self._error:
                return {
                    "schema": 1,
                    "scope": "application",
                    "paused": None,
                    "revision": self._state.revision,
                    "changed_at": self._state.changed_at,
                    "owner_account_id": self._state.owner_account_id,
                    "owner_generation": self._state.owner_generation,
                    "reason": self._state.reason,
                    "error": self._error,
                    **(
                        {"changed": False}
                        if changed is not None
                        else {}
                    ),
                }
            result = asdict(self._state)
            result["error"] = ""
            if changed is not None:
                result["changed"] = bool(changed)
            return result

    def current_snapshot(self) -> dict[str, Any]:
        """Return the in-process state without re-reading the setting file."""

        with self._condition:
            result = asdict(self._state)
            result["paused"] = (
                None if self._error else result["paused"]
            )
            result["error"] = self._error
            return result

    def _set_paused(
        self,
        paused: bool,
        *,
        account_id: str,
        generation: int,
        reason: str,
    ) -> dict[str, Any]:
        with self._condition:
            self._reload()
            current = self._state
            if current.paused == paused and not self._error:
                result = asdict(current)
                result["error"] = ""
                result["changed"] = False
                return result
            next_state = GlobalTaskPauseState(
                schema=1,
                scope="application",
                paused=bool(paused),
                revision=max(0, int(current.revision)) + 1,
                changed_at=datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                owner_account_id=str(account_id or ""),
                owner_generation=max(0, int(generation or 0)),
                reason=str(reason or "")[:500],
            )
            self._persist_locked(next_state)
            self._condition.notify_all()
            result = asdict(next_state)
            result["error"] = ""
            result["changed"] = True
            return result

    def pause(
        self,
        *,
        account_id: str,
        generation: int,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._set_paused(
            True,
            account_id=account_id,
            generation=generation,
            reason=reason,
        )

    def resume(
        self,
        *,
        account_id: str,
        generation: int,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._set_paused(
            False,
            account_id=account_id,
            generation=generation,
            reason=reason,
        )

    def ensure_ready(self, operation: str = "任务") -> None:
        snapshot = self.snapshot()
        error = str(snapshot.get("error") or "")
        if error:
            raise TaskPauseStateError(error)
        if snapshot["paused"] is True:
            raise TaskPauseError(
                f"全局操作已暂停，未启动新的{operation}"
            )

    def wait_until_ready(
        self,
        *,
        is_still_valid: Callable[[], bool] | None = None,
    ) -> bool:
        with self._condition:
            while True:
                if is_still_valid is not None and not is_still_valid():
                    return False
                if self._error:
                    raise TaskPauseStateError(self._error)
                if not self._state.paused:
                    return True
                self._condition.wait(timeout=0.5)

    def notify_waiters(self) -> None:
        with self._condition:
            self._condition.notify_all()
