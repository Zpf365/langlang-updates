from __future__ import annotations

import json
from pathlib import Path

from weimi_manager.dongxiao.buyer_flow.assistant_client import (
    BuyerAssistantClient,
)


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self) -> dict:
        return dict(self._payload)


def test_desktop_buyer_assistant_client_uses_bearer_actions() -> None:
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        action = kwargs["json"]["action"]
        if action == "create_pairing":
            return _Response({"pairing_id": "pair-1", "ticket": "ticket-1"})
        if action == "create_task":
            return _Response({"assistant_task_id": "assistant-1"})
        return _Response({"assistant_task_id": "assistant-1", "status": "running"})

    client = BuyerAssistantClient(
        base_url="https://example.invalid/functions/v1",
        access_token="desktop-token",
        post=post,
    )
    pairing = client.create_pairing("wx-shop-1")
    task = client.create_task(
        {
            "store_app_id": "wx-shop-1",
            "idempotency_key": "task-1",
            "requested_count": 5,
            "items": [],
        }
    )
    status = client.task_status("assistant-1")

    assert pairing["pairing_id"] == "pair-1"
    assert task["assistant_task_id"] == "assistant-1"
    assert status["status"] == "running"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer desktop-token"
    assert all(
        url.endswith("/buyer-assistant-desktop")
        for url, _kwargs in calls
    )


def test_miniprogram_has_ten_pages_and_official_store_components() -> None:
    root = Path(__file__).resolve().parent.parent / "buyer_miniprogram"
    app = json.loads((root / "app.json").read_text(encoding="utf-8"))
    assert len(app["pages"]) == 10
    for page in app["pages"]:
        assert (root / f"{page}.js").is_file()
        assert (root / f"{page}.wxml").is_file()
    task_wxml = (root / "pages/task-detail/index.wxml").read_text(
        encoding="utf-8"
    )
    tasks_wxml = (root / "pages/tasks/index.wxml").read_text(encoding="utf-8")
    assert "<store-product" in task_wxml
    assert 'product-id="{{current.product_id}}"' in task_wxml
    assert "<store-home" in tasks_wxml
    bind_js = (root / "pages/bind/index.js").read_text(encoding="utf-8")
    assert "wx.login" in bind_js
    assert "session_key" not in bind_js


def test_backend_keeps_secret_server_side_and_hashes_ticket_session() -> None:
    root = Path(__file__).resolve().parent.parent / "supabase"
    mini = (
        root / "functions/buyer-assistant-mini/index.ts"
    ).read_text(encoding="utf-8")
    desktop = (
        root / "functions/buyer-assistant-desktop/index.ts"
    ).read_text(encoding="utf-8")
    migration = (
        root / "migrations/202611190001_buyer_assistant.sql"
    ).read_text(encoding="utf-8")

    assert "WECHAT_BUYER_MINI_APPSECRET" in mini
    assert "jscode2session" in mini
    assert "session_key" not in mini
    assert "ticket_hash" in mini
    assert "mini_session_hash" in mini
    assert "getwxacodeunlimit" in desktop
    assert "WECHAT_BUYER_MINI_APPSECRET" in desktop
    assert "buyer_pairing_tickets" in migration
    assert "buyer_identities" in migration
    assert "buyer_assistant_tasks" in migration
    assert "buyer_order_matches" in migration


def test_miniprogram_config_contains_placeholders_not_real_secrets() -> None:
    root = Path(__file__).resolve().parent.parent / "buyer_miniprogram"
    config = (root / "config.js").read_text(encoding="utf-8")
    project = json.loads(
        (root / "project.config.json").read_text(encoding="utf-8")
    )
    assert "YOUR-PROJECT" in config
    assert "AppSecret" not in config
    assert project["appid"].startswith("wx000")
