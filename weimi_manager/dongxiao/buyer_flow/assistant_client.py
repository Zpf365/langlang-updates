from __future__ import annotations

from typing import Any, Callable

import requests


class BuyerAssistantClientError(RuntimeError):
    pass


class BuyerAssistantClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        timeout: float = 20.0,
        post: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.access_token = str(access_token)
        self.timeout = max(5.0, float(timeout))
        self._post = post or requests.post

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.access_token)

    def _request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise BuyerAssistantClientError("澜琅登录会话或买家助手后端未配置")
        try:
            response = self._post(
                f"{self.base_url}/buyer-assistant-desktop",
                json={"action": action, **payload},
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BuyerAssistantClientError(f"买家助手网络请求失败：{exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise BuyerAssistantClientError("买家助手后端返回格式不正确") from exc
        if not response.ok:
            raise BuyerAssistantClientError(
                str(data.get("error") or f"HTTP {response.status_code}")
            )
        if not isinstance(data, dict):
            raise BuyerAssistantClientError("买家助手后端返回格式不正确")
        return data

    def create_pairing(self, store_app_id: str) -> dict[str, Any]:
        return self._request(
            "create_pairing",
            {"store_app_id": str(store_app_id)},
        )

    def pairing_status(self, pairing_id: str) -> dict[str, Any]:
        return self._request(
            "pairing_status",
            {"pairing_id": str(pairing_id)},
        )

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._request("create_task", {"task": task})

    def task_status(self, assistant_task_id: str) -> dict[str, Any]:
        return self._request(
            "task_status",
            {"assistant_task_id": str(assistant_task_id)},
        )

    def record_order_match(
        self,
        *,
        local_run_id: str,
        platform_order_id: str,
        match_status: str,
        order_status: str,
        total_amount_cents: int,
        match_score: int = 100,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "record_order_match",
            {
                "local_run_id": str(local_run_id),
                "platform_order_id": str(platform_order_id),
                "match_status": str(match_status),
                "order_status": str(order_status),
                "total_amount_cents": int(total_amount_cents),
                "match_score": int(match_score),
                "detail": dict(detail or {}),
            },
        )

    def save_integration_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._request("save_integration_state", state)
