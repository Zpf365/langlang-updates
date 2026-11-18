from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg2


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="澜琅管理员卡密生成工具")
    parser.add_argument("--count", type=int, default=10, help="生成数量")
    parser.add_argument(
        "--type",
        choices=("temporary", "permanent"),
        default="temporary",
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-devices", type=int, default=1)
    parser.add_argument("--penalty-hours", type=int, default=24)
    parser.add_argument("--notes", default="")
    parser.add_argument("--available-days", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--project-ref",
        default=os.environ.get("LANGLANG_SUPABASE_PROJECT_REF", ""),
    )
    return parser.parse_args()


def create_code() -> str:
    text = "".join(secrets.choice(ALPHABET) for _ in range(20))
    return (
        f"LL-{text[0:5]}-{text[5:10]}-"
        f"{text[10:15]}-{text[15:20]}"
    )


def main() -> int:
    args = parse_args()
    project_ref = args.project_ref or input("Supabase 项目 Ref：").strip()
    password = os.environ.get("LANGLANG_SUPABASE_DB_PASSWORD")
    if not password:
        password = getpass.getpass("Supabase 数据库密码：")
    connection = psycopg2.connect(
        f"postgresql://postgres:{quote(password, safe='')}"
        f"@db.{project_ref}.supabase.co:5432/postgres?sslmode=require",
        connect_timeout=30,
    )
    count = max(1, min(1000, args.count))
    max_devices = max(1, min(999, args.max_devices))
    penalty_hours = max(0, min(720, args.penalty_hours))
    duration_days = (
        None
        if args.type == "permanent"
        else max(1, min(3650, args.days))
    )
    available_until = (
        f"now() + interval '{max(1, args.available_days)} days'"
        if args.available_days > 0
        else "NULL"
    )
    batch_id = (
        "batch_"
        + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "_"
        + secrets.token_hex(3)
    )
    rows: list[tuple[str, str]] = []
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "select secret_value from public.app_secrets "
                "where secret_key='card_hash_pepper'"
            )
            row = cursor.fetchone()
            if not row:
                print("数据库中没有卡密哈希密钥。")
                return 1
            pepper = row[0]
            for _index in range(count):
                code = create_code()
                code_hash = hmac.new(
                    pepper.encode("utf-8"),
                    code.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                cursor.execute(
                    f"""
                    insert into public.license_cards (
                        code_hash, code_hint, card_type, duration_days,
                        max_devices, rebind_penalty_hours, batch_id,
                        notes, available_until
                    )
                    values (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        {available_until}
                    )
                    """,
                    (
                        code_hash,
                        f"****-{code[-5:]}",
                        args.type,
                        duration_days,
                        max_devices,
                        penalty_hours,
                        batch_id,
                        args.notes,
                    ),
                )
                rows.append((code, code_hash))
    finally:
        connection.close()

    output = (
        Path(args.output).expanduser()
        if args.output
        else Path.cwd()
        / f"澜琅卡密_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["卡密", "类型", "天数", "设备数", "批次"])
        for code, _code_hash in rows:
            writer.writerow(
                [
                    code,
                    "永久" if args.type == "permanent" else "临时",
                    "" if duration_days is None else duration_days,
                    max_devices,
                    batch_id,
                ]
            )
    print(f"已生成 {len(rows)} 张卡密：{output}")
    print("完整卡密只在本次导出中出现，请妥善保存。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
