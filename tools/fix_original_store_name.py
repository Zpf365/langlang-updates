from __future__ import annotations

import os
import sqlite3
from pathlib import Path


database = Path(os.environ["APPDATA"]) / "wei_mi" / "wx_local.db"
connection = sqlite3.connect(database)
connection.execute(
    "update stores set name=? where appid=?",
    ("olevo宽栾商贸", "wx9542021397d12510"),
)
connection.commit()
connection.close()
print("ORIGINAL_STORE_NAME_UPDATED")
