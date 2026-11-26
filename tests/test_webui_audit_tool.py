from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _audit_module():
    path = (
        Path(__file__).parents[1]
        / "tools"
        / "generate_webui_parity_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_webui_parity_audit",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_audit_follows_nested_page_component_imports() -> None:
    surfaces, orphan_components = _audit_module().reachable_surfaces()

    assert "pages/ActivityCenterPage.vue" in surfaces
    assert "pages/OrdersParityPage.vue" in surfaces
    assert "components/OrderTableRow.vue" in surfaces
    assert "pages/ActivityCenterPage.vue" not in orphan_components
