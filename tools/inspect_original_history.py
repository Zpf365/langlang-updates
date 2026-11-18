from __future__ import annotations

import os
import sqlite3
from pathlib import Path


root = Path(os.environ["APPDATA"]) / "wei_mi" / "browser"
for history in root.glob("*/*/Default/History"):
    try:
        connection = sqlite3.connect(f"file:{history}?mode=ro", uri=True)
        rows = connection.execute(
            "select url, title from urls order by last_visit_time desc limit 100"
        ).fetchall()
        print("HISTORY", history)
        for url, title in rows:
            if "store.weixin.qq.com" in url:
                print(title, url)
        connection.close()
    except Exception as exc:
        print("READ_FAILED", history, type(exc).__name__)
