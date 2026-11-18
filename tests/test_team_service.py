from __future__ import annotations

import unittest
from types import SimpleNamespace

from weimi_manager.team_service import TeamService
from weimi_manager.ui.team_page import TeamPage


class FakeTeamClient:
    def __init__(self) -> None:
        self.calls = []

    def list_team_members(self, access_token):
        self.calls.append(("list", access_token))
        return [{"username": "ZPF", "role": "owner"}]

    def team_admin(self, access_token, payload):
        self.calls.append(("admin", access_token, payload))
        return {"ok": True}


class TeamServiceTests(unittest.TestCase):
    def test_create_subaccount_sends_role_permissions_and_grants(self) -> None:
        client = FakeTeamClient()
        service = TeamService(client, "access")

        service.create_subaccount(
            username="ZPF:01",
            password="password123",
            role="operator",
            permissions={"stores_view": True, "products_upload": True},
            grants=["wx-one", "wx-two"],
        )

        payload = client.calls[0][2]
        self.assertEqual(payload["action"], "create_subaccount")
        self.assertEqual(payload["username"], "ZPF:01")
        self.assertEqual(payload["role"], "operator")
        self.assertEqual(payload["grants"], ["wx-one", "wx-two"])

    def test_reset_password_is_owner_driven(self) -> None:
        client = FakeTeamClient()
        service = TeamService(client, "access")

        service.reset_password("member-1", "new-password")

        payload = client.calls[0][2]
        self.assertEqual(payload["action"], "reset_password")
        self.assertEqual(payload["member_id"], "member-1")

    def test_team_page_refresh_supports_run_task_progress_argument(
        self,
    ) -> None:
        calls = []
        captured = {}

        class FakeService:
            @staticmethod
            def list_members():
                calls.append("list")
                return [{"username": "ZPF", "role": "owner"}]

        class FakeWindow:
            @staticmethod
            def run_task(work, _done, _error, **_kwargs):
                captured["work"] = work
                return True

        page = object.__new__(TeamPage)
        page.window = FakeWindow()
        page._service = lambda: FakeService()
        page.refresh()
        captured["work"](SimpleNamespace())
        self.assertEqual(calls, ["list"])


if __name__ == "__main__":
    unittest.main()
