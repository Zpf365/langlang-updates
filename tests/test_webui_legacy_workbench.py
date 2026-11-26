from __future__ import annotations

from types import SimpleNamespace

from weimi_manager.webui import legacy_workbench


def test_legacy_workbench_uses_the_order_workbench_entrypoint(
    monkeypatch,
) -> None:
    created: list[str] = []

    class FakeRoot:
        def withdraw(self) -> None:
            created.append("withdraw")

        def after(self, _delay: int, callback) -> None:
            callback()

        def mainloop(self) -> None:
            created.append("mainloop")

        def destroy(self) -> None:
            created.append("destroy")

    class FakeWorkbench:
        def protocol(self, name: str, _callback) -> None:
            created.append(name)

    root = FakeRoot()
    workbench = FakeWorkbench()
    monkeypatch.setattr(
        legacy_workbench,
        "AppPaths",
        SimpleNamespace(discover=lambda: object()),
    )
    monkeypatch.setattr(
        "weimi_manager.ui.app.MainWindow",
        lambda _paths: root,
    )
    monkeypatch.setattr(
        "weimi_manager.ui.order_workbench.open_order_workbench",
        lambda parent, window: created.append(
            "open" if parent is root and window is root else "wrong-parent"
        )
        or workbench,
    )

    assert legacy_workbench.main() == 0
    assert created == ["withdraw", "open", "WM_DELETE_WINDOW", "mainloop"]
