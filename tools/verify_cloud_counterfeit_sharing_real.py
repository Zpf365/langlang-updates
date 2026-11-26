from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.auth_client import SupabaseAuthClient
from weimi_manager.auth_config import AuthConfig
from weimi_manager.auth_service import build_auth_service
from weimi_manager.config import AppPaths
from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


EVIDENCE = ROOT / "deliveries" / "1.8.85" / "cloud-counterfeit-real-evidence.json"
ACTIVE_PREFIX = ""


def digest(rows: list[dict]) -> str:
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def auth_context():
    base = AppPaths.discover()
    config = AuthConfig.load(base)
    auth_candidates = [
        base.data_dir.parent / "auth",
        Path(os.environ.get("LOCALAPPDATA") or "") / "LangLang" / "auth",
    ]
    service = None
    accounts = []
    for auth_dir in auth_candidates:
        if not (auth_dir / "saved_accounts.bin").is_file():
            continue
        candidate_service = build_auth_service(config, auth_dir)
        if candidate_service is None:
            continue
        candidate_accounts = candidate_service.store.list_accounts()
        if candidate_accounts:
            service = candidate_service
            accounts = candidate_accounts
            break
    if service is None:
        raise RuntimeError("正式 AuthService 不可用")
    if not accounts:
        raise RuntimeError("本机没有可恢复的正式账户会话")
    context = service.restore_account(accounts[0])
    if not context.organization_id:
        raise RuntimeError("当前正式账户不属于组织")
    return config, context


def bound_client(config: AuthConfig, context) -> SupabaseAuthClient:
    client = SupabaseAuthClient(config)
    client.bind_session(context.tokens, lambda _tokens: None)
    return client


def client_controller(
    root: Path,
    name: str,
    client: SupabaseAuthClient,
    context,
) -> NextUiController:
    database = Database(root / f"{name}.db")
    store_id = database.add_store(
        f"AUTOTEST-{name}",
        f"wx-autotest-{uuid.uuid4().hex[:12]}",
        "",
        "AUTOTEST",
    )
    return NextUiController(
        database,
        auth_client=client,
        auth_context=context,
        project_dir=ROOT,
    )


def cleanup(prefix: str) -> None:
    escaped = prefix.replace("'", "''")
    sql = (
        "delete from public.organization_counterfeit_violations "
        f"where title like '{escaped}%' "
        "returning dedupe_key, title;"
    )
    completed = subprocess.run(
        [
            shutil.which("npx") or shutil.which("npx.cmd") or "npx.cmd",
            "supabase",
            "db",
            "query",
            "--remote",
            "--output-format",
            "json",
            "--yes",
            sql,
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "AUTOTEST 清理失败，未输出连接凭据；"
            f" stderr={completed.stderr[-2000:]}"
        )


def execute() -> dict:
    global ACTIVE_PREFIX
    run_id = uuid.uuid4().hex[:12].upper()
    prefix = f"AUTOTEST-{run_id}"
    ACTIVE_PREFIX = prefix
    title_a = f"{prefix}-云售假跨店标题"
    title_clean = f"{prefix}-普通干净标题"
    title_other = f"{prefix}-质量不合格"
    title_d1 = f"{prefix}-并发A"
    title_d2 = f"{prefix}-并发B"
    config, context = auth_context()
    client_a = bound_client(config, context)
    client_b = bound_client(config, context)
    token = context.tokens.access_token
    before = client_a.pull_organization_counterfeit_violations(token)
    before_count = len(before)
    before_hash = digest(before)

    with tempfile.TemporaryDirectory(prefix="langlang-cloud-autotest-") as raw:
        root = Path(raw)
        controller_a = client_controller(
            root,
            "client-a",
            client_a,
            context,
        )
        controller_b = client_controller(
            root,
            "client-b",
            client_b,
            context,
        )
        store_a = controller_a.db.list_stores()[0]
        local_records = [
            {
                "record_id": f"{prefix}-record-A",
                "title": title_a,
                "reason": "平台同步：出售假冒商品",
                "product_id": f"{prefix}-product-A",
                "store_id": store_a.id,
                "store_app_id": store_a.app_id,
                "store_name": "AUTOTEST-不得上云店铺名",
                "raw_json": {
                    "buyer_name": "AUTOTEST-买家",
                    "phone": "13800138000",
                    "address": "AUTOTEST-地址",
                    "cookie": "AUTOTEST-COOKIE",
                },
            },
            {
                "record_id": f"{prefix}-record-other",
                "title": title_other,
                "reason": "质量不合格",
                "product_id": f"{prefix}-product-other",
                "store_id": store_a.id,
                "store_app_id": store_a.app_id,
                "store_name": "AUTOTEST-不得上云店铺名",
            },
        ]
        controller_a.db.set_setting(
            "violation_library",
            json.dumps(local_records, ensure_ascii=False),
        )
        pushed = controller_a._push_counterfeit_violation_index(token)
        after_first_push = client_b.pull_organization_counterfeit_violations(
            token
        )
        row_a = next(
            (
                row for row in after_first_push
                if row.get("title") == title_a
            ),
            None,
        )
        if row_a is None:
            raise RuntimeError("客户端 B 未读取到客户端 A 的云售假记录")

        controller_b.db.set_setting(
            "upload_batch",
            json.dumps(
                [
                    {
                        "batch_id": "autotest-blocked",
                        "title": title_a,
                        "platform": "淘宝",
                        "source_product_id": f"{prefix}-source-A",
                        "price": 100,
                        "stock": 5,
                        "payload": {
                            "head_imgs": ["https://example.test/head.jpg"],
                            "skus": [
                                {"sale_price": 100, "stock_num": 5}
                            ],
                        },
                    },
                    {
                        "batch_id": "autotest-clean",
                        "title": title_clean,
                        "platform": "淘宝",
                        "source_product_id": f"{prefix}-source-clean",
                        "price": 120,
                        "stock": 3,
                        "payload": {
                            "head_imgs": ["https://example.test/clean.jpg"],
                            "skus": [
                                {"sale_price": 120, "stock_num": 3}
                            ],
                        },
                    },
                ],
                ensure_ascii=False,
            ),
        )
        controller_b.db.set_setting(
            "upload_templates",
            json.dumps(
                [
                    {
                        "id": "autotest-cloud",
                        "name": "AUTOTEST 云违规过滤",
                        "cloud_violation": True,
                    }
                ],
                ensure_ascii=False,
            ),
        )
        controller_b._pull_counterfeit_violation_index(token)
        preflight = controller_b.dispatch(
            "upload.batch.preflight",
            {
                "batch_ids": ["autotest-blocked", "autotest-clean"],
                "template_id": "autotest-cloud",
            },
        )
        blocked = next(
            row for row in preflight["rows"]
            if row["batch_id"] == "autotest-blocked"
        )
        clean = next(
            row for row in preflight["rows"]
            if row["batch_id"] == "autotest-clean"
        )
        if not any("云违规标题过滤命中" in issue for issue in blocked["issues"]):
            raise RuntimeError("客户端 B 上货预检未阻断云售假标题")
        if any("云违规" in issue for issue in clean["issues"]):
            raise RuntimeError("客户端 B 上货预检误杀普通干净标题")

        same_key = str(row_a["dedupe_key"])
        row_same_a = {
            **row_a,
            "title": title_a,
            "normalized_title": title_a.casefold(),
        }
        row_same_b = dict(row_same_a)
        row_d1 = {
            "dedupe_key": f"{store_a.app_id}:{prefix}-D1:{title_d1.casefold()}",
            "normalized_title": title_d1.casefold(),
            "title": title_d1,
            "reason_category": "售假",
            "product_id": f"{prefix}-D1",
            "store_app_id": store_a.app_id,
        }
        row_d2 = {
            "dedupe_key": f"{store_a.app_id}:{prefix}-D2:{title_d2.casefold()}",
            "normalized_title": title_d2.casefold(),
            "title": title_d2,
            "reason_category": "出售假冒商品",
            "product_id": f"{prefix}-D2",
            "store_app_id": store_a.app_id,
        }

        def upsert(client, rows):
            return client.upsert_organization_counterfeit_violations(
                token,
                rows,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(upsert, client_a, [row_same_a]),
                executor.submit(upsert, client_b, [row_same_b]),
                executor.submit(upsert, client_a, [row_d1]),
                executor.submit(upsert, client_b, [row_d2]),
            ]
            for future in futures:
                future.result()

        merged = client_a.pull_organization_counterfeit_violations(token)
        same_rows = [
            row for row in merged
            if row.get("dedupe_key") == same_key
        ]
        if len(same_rows) != 1:
            raise RuntimeError("并发同 dedupe_key 产生了重复记录")
        if int(same_rows[0].get("revision") or 0) < 3:
            raise RuntimeError("并发同 dedupe_key 的 revision 未递增")
        if not all(
            any(row.get("title") == title for row in merged)
            for title in (title_d1, title_d2)
        ):
            raise RuntimeError("并发不同 dedupe_key 互相覆盖")
        after_count = len(merged)
        after_hash = digest(merged)
        forbidden = {
            "raw_json",
            "buyer_name",
            "phone",
            "address",
            "cookie",
            "token",
            "app_secret",
            "store_name",
        }
        remote_fields = {
            key
            for row in merged
            if str(row.get("title") or "").startswith(prefix)
            for key in row
        }
        leaked_fields = sorted(forbidden.intersection(remote_fields))
        if leaked_fields:
            raise RuntimeError(
                "云售假记录包含禁止字段：" + ", ".join(leaked_fields)
            )

        result = {
            "run_id": run_id,
            "marker": prefix,
            "organization_id_present": bool(context.organization_id),
            "before_count": before_count,
            "before_hash": before_hash,
            "first_push_saved": int(pushed.get("saved") or 0),
            "cross_client_row": {
                key: row_a.get(key)
                for key in (
                    "title",
                    "reason_category",
                    "dedupe_key",
                    "revision",
                    "store_app_id",
                )
            },
            "preflight": {
                "blocked": blocked["issues"],
                "clean_issues": clean["issues"],
                "ready": preflight["ready"],
                "blocked_count": preflight["blocked"],
            },
            "concurrent_same_key_revision": same_rows[0].get("revision"),
            "concurrent_distinct_keys": [title_d1, title_d2],
            "other_violation_remote_count": sum(
                1 for row in merged if row.get("title") == title_other
            ),
            "remote_forbidden_fields": leaked_fields,
            "after_count": after_count,
            "after_hash": after_hash,
        }

    cleanup(prefix)
    final_rows = client_b.pull_organization_counterfeit_violations(token)
    result["cleanup"] = {
        "final_count": len(final_rows),
        "final_hash": digest(final_rows),
        "baseline_restored": (
            len(final_rows) == before_count
            and digest(final_rows) == before_hash
        ),
        "remaining_autotest_rows": sum(
            1
            for row in final_rows
            if str(row.get("title") or "").startswith(prefix)
        ),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Use --execute to run the authorized organization RPC test.")
        return 0
    try:
        result = execute()
    except Exception:
        if ACTIVE_PREFIX:
            try:
                cleanup(ACTIVE_PREFIX)
            except Exception:
                pass
        raise
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "before_count": result["before_count"],
                "after_count": result["after_count"],
                "baseline_restored": result["cleanup"]["baseline_restored"],
                "evidence": str(EVIDENCE),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
