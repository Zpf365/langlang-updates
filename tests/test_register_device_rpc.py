from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"


def _latest_register_my_device_definition() -> str:
    definitions: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        marker = "create or replace function public.register_my_device("
        start = text.lower().find(marker)
        if start < 0:
            continue
        end = text.find("\n$$;", start)
        assert end >= 0, f"{path.name}: register_my_device 函数未闭合"
        definitions.append(text[start : end + 4])
    assert definitions, "没有找到 register_my_device 定义"
    return definitions[-1]


def test_register_device_reactivates_revoked_row_instead_of_reinserting() -> None:
    definition = _latest_register_my_device_definition()
    normalized = " ".join(definition.lower().split())
    lookup = normalized.split("select * into v_device", 1)[1].split(
        "if found",
        1,
    )[0]

    assert "and revoked_at is null" not in lookup
    assert "revoked_at = null" in normalized
