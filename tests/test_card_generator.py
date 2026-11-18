from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace

import pytest

from weimi_manager.auth_client import AuthError
from weimi_manager.card_generator import AdminCardService, CardGeneratorWindow


class FakeAdminClient:
    def __init__(self) -> None:
        self.generated: dict | None = None

    @staticmethod
    def login_by_username(username: str, password: str):
        assert username == "admin"
        assert password == "secret"
        return SimpleNamespace(access_token="admin-token")

    def generate_license_cards(self, access_token: str, **payload):
        self.generated = {
            "access_token": access_token,
            **payload,
        }
        return {
            "batch_id": "batch_test",
            "duration_days": payload["duration_days"],
            "codes": ["LL-AAAAA-BBBBB-CCCCC-DDDDD"],
        }


def test_admin_login_does_not_require_device_registration() -> None:
    client = FakeAdminClient()
    tokens = AdminCardService(client).login("admin", "secret")
    assert tokens.access_token == "admin-token"


def test_generate_batch_validates_and_forwards_settings() -> None:
    client = FakeAdminClient()
    service = AdminCardService(client)
    tokens = service.login("admin", "secret")
    batch = service.generate(
        tokens,
        count=999,
        card_type="temporary",
        duration_days=5000,
        max_devices=0,
        rebind_penalty_hours=999,
        notes="  test market  ",
        available_days=-1,
    )

    assert client.generated == {
        "access_token": "admin-token",
        "count": 500,
        "card_type": "temporary",
        "duration_days": 3650,
        "max_devices": 1,
        "rebind_penalty_hours": 720,
        "notes": "test market",
        "available_days": 0,
    }
    assert batch.batch_id == "batch_test"
    assert batch.codes == ["LL-AAAAA-BBBBB-CCCCC-DDDDD"]


def test_generate_rejects_empty_server_response() -> None:
    class EmptyClient(FakeAdminClient):
        def generate_license_cards(self, _access_token: str, **_payload):
            return {"codes": []}

    with pytest.raises(AuthError, match="没有返回可用卡密"):
        AdminCardService(EmptyClient()).generate(
            SimpleNamespace(access_token="admin-token"),
            count=1,
            card_type="permanent",
            duration_days=30,
            max_devices=1,
            rebind_penalty_hours=24,
        )


def test_login_fields_do_not_overlap_login_button() -> None:
    try:
        window = CardGeneratorWindow(AdminCardService(FakeAdminClient()))
    except tk.TclError as exc:
        pytest.skip(str(exc))
    try:
        window.withdraw()
        username_row = int(window.login_focus.grid_info()["row"])
        password_row = int(window.password_focus.grid_info()["row"])
        button_row = int(window.login_button.grid_info()["row"])
        assert username_row == 3
        assert password_row == 5
        assert button_row == 6
        assert len({username_row, password_row, button_row}) == 3
    finally:
        window.destroy()
