from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import AppPaths
from .db import Database


def parse_credential_text(text: str) -> tuple[str, str]:
    app_match = re.search(
        r"(?:^\s*|\n\s*)(?:ID|AppID|AppId|Appid|appid)\s*[:：=]?\s*([A-Za-z0-9_-]{8,})",
        text,
        re.MULTILINE,
    )
    secret_match = re.search(
        r"(?:^\s*|\n\s*)(?:AppSecret|APPSecret|Secret|secret|密钥)\s*[:：=]?\s*([A-Za-z0-9_-]{16,})",
        text,
        re.MULTILINE,
    )
    if not app_match or not secret_match:
        raise ValueError("未能从文本文件中识别 ID 和 AppSecret")
    return app_match.group(1), secret_match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从本地文本文件安全导入店铺凭据")
    parser.add_argument("path", help="包含 ID 和 AppSecret 的文本文件")
    parser.add_argument("--name", default="测试店铺", help="本地店铺名称")
    args = parser.parse_args(argv)

    source = Path(args.path).expanduser().resolve()
    if not source.is_file():
        print("未找到凭据文本文件。")
        return 2
    app_id, secret = parse_credential_text(source.read_text(encoding="utf-8-sig"))
    paths = AppPaths.discover()
    db = Database(paths.database)
    existing = next((store for store in db.list_stores() if store.app_id == app_id), None)
    if existing:
        db.update_store(existing.id, args.name, "测试分组", secret)
        store_id = existing.id
        action = "更新"
    else:
        store_id = db.add_store(args.name, app_id, secret, "测试分组")
        action = "导入"
    print(f"{action}成功：{args.name}（本机 ID {store_id}）")
    print("AppSecret 已使用 Windows DPAPI 加密保存，未在终端输出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
