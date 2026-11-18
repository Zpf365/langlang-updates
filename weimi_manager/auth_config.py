from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import AppPaths


class AuthConfigError(RuntimeError):
    pass


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_json(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _looks_configured(url: str, publishable_key: str) -> bool:
    normalized_url = url.strip().lower()
    normalized_key = publishable_key.strip()
    if not normalized_url.startswith("https://"):
        return False
    if not normalized_key or "YOUR_" in normalized_key.upper():
        return False
    return not any(
        marker in normalized_url
        for marker in ("your-project", "example.supabase.co", "replace-me")
    )


def _config_candidates(paths: AppPaths) -> tuple[Path, ...]:
    candidates = [
        paths.resource_dir / "assets" / "auth_config.json",
        paths.data_dir.parent / "auth_config.json",
        paths.project_dir / "auth_config.json",
        paths.project_dir / "_internal" / "assets" / "auth_config.json",
    ]
    try:
        backups = sorted(
            paths.project_dir.parent.glob("LangLang.update-backup-*"),
            key=lambda path: path.name,
            reverse=True,
        )
    except OSError:
        backups = []
    candidates.extend(
        backup / "_internal" / "assets" / "auth_config.json"
        for backup in backups[:5]
    )
    return tuple(candidates)


@dataclass(frozen=True)
class AuthConfig:
    supabase_url: str
    publishable_key: str
    request_timeout: float = 20.0

    @property
    def configured(self) -> bool:
        return _looks_configured(self.supabase_url, self.publishable_key)

    @property
    def base_url(self) -> str:
        return self.supabase_url.rstrip("/")

    def auth_url(self, path: str) -> str:
        return f"{self.base_url}/auth/v1/{path.lstrip('/')}"

    def rest_url(self, path: str) -> str:
        return f"{self.base_url}/rest/v1/{path.lstrip('/')}"

    def function_url(self, name: str) -> str:
        return f"{self.base_url}/functions/v1/{name.lstrip('/')}"

    @classmethod
    def load(cls, paths: AppPaths) -> "AuthConfig":
        env_file = _read_env_file(paths.project_dir / ".env.local")
        env_values = {**env_file, **os.environ}
        url = str(
            env_values.get("LANGLANG_SUPABASE_URL")
            or ""
        ).strip()
        key = str(
            env_values.get("LANGLANG_SUPABASE_PUBLISHABLE_KEY")
            or env_values.get("LANGLANG_SUPABASE_ANON_KEY")
            or ""
        ).strip()
        if not url and not key:
            for path in _config_candidates(paths):
                payload = _read_json(path)
                if not payload:
                    continue
                url = str(
                    payload.get("supabase_url")
                    or payload.get("url")
                    or ""
                ).strip()
                key = str(
                    payload.get("publishable_key")
                    or payload.get("anon_key")
                    or ""
                ).strip()
                if url or key:
                    break
        try:
            timeout = max(
                5.0,
                min(
                    60.0,
                    float(env_values.get("LANGLANG_AUTH_TIMEOUT", "20")),
                ),
            )
        except (TypeError, ValueError):
            timeout = 20.0
        return cls(
            supabase_url=url,
            publishable_key=key,
            request_timeout=timeout,
        )

    def require_configured(self) -> None:
        if not self.configured:
            raise AuthConfigError(
                "远程账号服务尚未配置。请填写 Supabase 项目地址和公开客户端密钥。"
            )
