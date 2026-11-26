from __future__ import annotations

from ..config import AppPaths


def main() -> int:
    from ..ui.app import MainWindow
    from ..ui.order_workbench import open_order_workbench

    paths = AppPaths.discover()
    root = MainWindow(paths)
    root.withdraw()

    def show_workbench() -> None:
        workbench = open_order_workbench(root, root)
        workbench.protocol("WM_DELETE_WINDOW", root.destroy)

    root.after(50, show_workbench)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
