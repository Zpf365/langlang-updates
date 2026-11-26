from __future__ import annotations

from weimi_manager.startup_update import run_startup_update

if run_startup_update():
    raise SystemExit(0)

from weimi_manager.webui.main import main

raise SystemExit(main())
