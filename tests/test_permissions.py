from __future__ import annotations

import unittest
from types import SimpleNamespace

from weimi_manager.ui.app import MainWindow


def _window(context=None):
    window = object.__new__(MainWindow)
    window.auth_context = context
    return window


class WindowPermissionTests(unittest.TestCase):
    def test_owner_and_admin_receive_full_page_access(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                window = _window(
                    SimpleNamespace(
                        organization_id="org-1",
                        member_role=role,
                        permissions={},
                    )
                )
                self.assertTrue(window._can_permission("team_manage"))
                self.assertTrue(window._can_permission("stores_delete"))

    def test_operator_requires_explicit_permission(self) -> None:
        window = _window(
            SimpleNamespace(
                organization_id="org-1",
                member_role="operator",
                permissions={
                    "stores_view": True,
                    "team_manage": True,
                },
            )
        )
        self.assertTrue(window._can_permission("stores_view"))
        self.assertFalse(window._can_permission("stores_delete"))
        self.assertFalse(window._can_permission("team_manage"))

    def test_development_window_without_context_keeps_full_access(self) -> None:
        window = _window()
        self.assertTrue(window._can_permission("stores_delete"))
        self.assertTrue(window._can_permission("team_manage"))


if __name__ == "__main__":
    unittest.main()
