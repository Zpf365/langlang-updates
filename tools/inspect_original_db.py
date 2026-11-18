from __future__ import annotations

import os
import sqlite3
from pathlib import Path


db_path = Path(os.environ["APPDATA"]) / "wei_mi" / "wx_local.db"
connection = sqlite3.connect(db_path)
connection.row_factory = sqlite3.Row
tables = [
    row["name"]
    for row in connection.execute(
        "select name from sqlite_master where type='table' order by name"
    )
]
print("database", db_path)
for table in tables:
    columns = [
        (row["name"], row["type"])
        for row in connection.execute(f'pragma table_info("{table}")')
    ]
    count = connection.execute(f'select count(*) from "{table}"').fetchone()[0]
    print("TABLE", table, "COUNT", count)
    print("COLUMNS", columns)
connection.close()
