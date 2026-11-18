from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from pathlib import Path


credential_path = Path(r"D:\GPT\新建 文本文档.txt")
text = credential_path.read_text(encoding="utf-8-sig")
appid = re.search(
    r"(?:^|\n)\s*ID\s*[:：=]?\s*([A-Za-z0-9_-]{8,})",
    text,
    re.MULTILINE,
).group(1)
secret = re.search(
    r"(?:^|\n)\s*(?:AppSecret|Secret|密钥)\s*[:：=]?\s*([A-Za-z0-9_-]{16,})",
    text,
    re.MULTILINE,
).group(1)

db_path = Path(os.environ["APPDATA"]) / "wei_mi" / "wx_local.db"
connection = sqlite3.connect(db_path)
connection.row_factory = sqlite3.Row
existing = connection.execute(
    "select id from stores where appid=?",
    (appid,),
).fetchone()
if existing:
    print("Original store already exists.")
    connection.close()
    raise SystemExit(0)

account = connection.execute(
    "select account from accounts order by id limit 1"
).fetchone()
if account is None:
    connection.close()
    raise SystemExit("Original application has no local account owner.")

store_id = uuid.uuid4().hex
connection.execute(
    """
    insert into stores
        (id, name, appid, secret, delivery_id, site_code,
         ewaybill_acct_id, shop_id, template_id, sender, templates,
         sort, owner, grp, cost_rate, wm1688_account_id)
    values (?, ?, ?, ?, '', '', '', '', '', '', '', 0, ?, ?, '', null)
    """,
    (store_id, "测试店铺", appid, secret, account["account"], "测试分组"),
)
connection.commit()
connection.close()
print("Original store added.")
