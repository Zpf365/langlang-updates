from __future__ import annotations

from pathlib import Path

from .config import AppPaths
from .db import Database


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key.strip()] = value
    return result


def main() -> int:
    paths = AppPaths.discover()
    values = _read_env(paths.project_dir / ".env.local")
    app_id = values.get("WECHAT_APPID", "").strip()
    secret = values.get("WECHAT_SECRET", "").strip()
    name = values.get("WECHAT_STORE_NAME", "测试店铺").strip() or "测试店铺"
    if not app_id or not secret:
        print("未导入：.env.local 中的 WECHAT_APPID 或 WECHAT_SECRET 为空。")
        return 2

    db = Database(paths.database)
    existing = next((store for store in db.list_stores() if store.app_id == app_id), None)
    if existing:
        db.update_store(existing.id, name, existing.group_name, secret)
        print(f"已更新本机店铺配置：{name}")
    else:
        db.add_store(name, app_id, secret, "测试分组")
        print(f"已导入本机店铺：{name}")
    print("AppSecret 已使用 Windows DPAPI 加密保存，未输出到终端。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
