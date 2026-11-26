from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path

import pytest

from weimi_manager import tester_auth
from weimi_manager.auth_client import AuthError
from weimi_manager.config import account_data_directory
from weimi_manager.ui.auth_window import AuthWindow

PASSWORD = "virtual-test-only-2026"


def _virtual_accounts() -> list[dict[str, str]]:
    accounts = []
    for index in range(1, 21):
        salt = f"{index:032x}"
        accounts.append(
            {
                "username": f"virtual{index}",
                "account_id": f"tester-zpf{index}",
                "email": f"virtual{index}@example.invalid",
                "salt": salt,
                "password_hash": tester_auth.TesterAuthService._hash(
                    PASSWORD,
                    salt,
                ),
            }
        )
    return accounts


def _service(tmp_path: Path) -> tester_auth.TesterAuthService:
    assets = tmp_path / "resources" / "assets"
    assets.mkdir(parents=True)
    (assets / "tester_accounts.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "accounts": _virtual_accounts(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tester_auth.TesterAuthService(
        tmp_path / "resources",
        tmp_path / "data",
    )


def test_twenty_virtual_accounts_login_without_real_credentials(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    for index in range(1, 21):
        context = service.login(
            f"virtual{index}",
            PASSWORD,
            False,
        )
        assert context.account_id == f"tester-zpf{index}"
        assert context.license.permanent is True


def test_virtual_account_paths_and_saved_password_round_trip(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    context = service.login("virtual1", PASSWORD, True)
    expected = {
        "tester-zpf1": "682bb21145c0805951ea10e8",
        "tester-zpf20": "32aa9cccfc5389d46c584d95",
    }
    for account_id, expected_name in expected.items():
        path = account_data_directory(
            tmp_path / "data",
            account_id,
        )
        assert path.parent.name == expected_name
    saved = service.store.list_accounts()
    assert len(saved) == 1
    assert saved[0].account_id == context.account_id
    assert saved[0].password == PASSWORD


def test_virtual_account_wrong_password_is_rejected(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(AuthError) as raised:
        service.login("virtual1", "wrong-virtual-password", False)

    assert raised.value.status == 401


def test_virtual_auth_window_accepts_account(
    tmp_path: Path,
) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    service = _service(tmp_path)
    try:
        window = AuthWindow(
            type(
                "Paths",
                (),
                {"resource_dir": tmp_path / "resources"},
            )(),
            service,
        )
        window.withdraw()
        window.login_account.insert(0, "virtual1")
        window.login_password.insert(0, PASSWORD)
        window._login()
        deadline = time.monotonic() + 5
        while window.result is None and time.monotonic() < deadline:
            try:
                window.update()
            except tk.TclError:
                break
            time.sleep(0.01)
        assert window.result is not None
        assert window.result.account_id == "tester-zpf1"
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
