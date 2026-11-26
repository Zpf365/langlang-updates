from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .adapter import REPORT_ENDPOINTS


class AnalyticsTransportError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True, slots=True)
class ReportResponse:
    accepted_event_ids: tuple[str, ...]
    rejected_event_ids: tuple[str, ...]


class AnalyticsApiClient:
    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], str],
        *,
        timeout_seconds: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        base = str(base_url or "").strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("运营后台基址无效")
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("运营后台必须使用 HTTPS")
        self.base_url = base
        self.dispatcher_mode = parsed.path.rstrip("/").endswith(
            "/functions/v1/ops-report"
        )
        self.token_provider = token_provider
        self.timeout_seconds = max(1, min(30, int(timeout_seconds)))
        self.session = session or requests.Session()

    def send(
        self,
        endpoint_key: str,
        events: list[dict[str, Any]],
        *,
        batch_id: str,
    ) -> ReportResponse:
        endpoint = REPORT_ENDPOINTS.get(endpoint_key)
        if not self.dispatcher_mode and endpoint is None:
            raise AnalyticsTransportError("invalid_endpoint")
        token = str(self.token_provider() or "").strip()
        if not token:
            raise AnalyticsTransportError("auth_unavailable")
        if not events:
            return ReportResponse((), ())
        body = {
            "schema_version": 1,
            "batch_id": str(batch_id),
            "events": events[:100],
        }
        destination = (
            self.base_url
            if self.dispatcher_mode
            else f"{self.base_url}{endpoint}"
        )
        try:
            response = self.session.post(
                destination,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise AnalyticsTransportError("timeout") from exc
        except requests.RequestException as exc:
            raise AnalyticsTransportError("network") from exc
        status = int(response.status_code)
        if status in {401, 403}:
            raise AnalyticsTransportError("auth_rejected")
        if status == 429:
            raise AnalyticsTransportError("rate_limited")
        if status >= 500:
            raise AnalyticsTransportError("server_error")
        if status < 200 or status >= 300:
            raise AnalyticsTransportError(f"http_{status}")
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AnalyticsTransportError("malformed_response") from exc
        if not isinstance(data, dict):
            raise AnalyticsTransportError("malformed_response")
        accepted = data.get("accepted_event_ids")
        rejected = data.get("rejected_event_ids")
        if accepted is None and isinstance(data.get("events"), list):
            accepted = [
                item.get("event_id")
                for item in data["events"]
                if isinstance(item, dict) and item.get("accepted") is True
            ]
            rejected = [
                item.get("event_id")
                for item in data["events"]
                if isinstance(item, dict) and item.get("accepted") is False
            ]
        if not isinstance(accepted, list):
            raise AnalyticsTransportError("contract_missing_ack")
        return ReportResponse(
            tuple(str(value) for value in accepted if value),
            tuple(
                str(value)
                for value in (rejected or [])
                if value
            ),
        )
