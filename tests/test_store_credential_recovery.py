from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path

from openpyxl import Workbook

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, Product
from weimi_manager.wechat_api import WechatApiError
from weimi_manager.webui.controller import NextUiController


def app_id(number: int) -> str:
    return f"wx{number:016d}"


def secret(number: int) -> str:
    return f"Secret-{number:024d}"


class CredentialService:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[int, str]] = []
        self.refresh_calls: list[int] = []

    def validate_store_credential(self, store, current_secret: str) -> str:
        self.calls.append((store.id, current_secret))
        result = self.responses.get(current_secret, "连接成功")
        if isinstance(result, Exception):
            raise result
        return str(result)

    def refresh_store_metrics(self, store) -> dict:
        self.refresh_calls.append(store.id)
        return {
            "_credential_checked": True,
            "_credential_probe_ok": True,
            "_credential_error_code": "",
            "_credential_error_message": "",
        }


def preview_payload(
    text: str,
    *,
    source_kind: str = "paste",
    source_name: str = "paste.txt",
) -> dict:
    return {
        "source_kind": source_kind,
        "source_name": source_name,
        "text": text,
    }


def test_store_credential_schema_and_state_rows_are_additive(tmp_path: Path) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("迁移店铺", app_id(1), secret(1))

    with database.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        state = conn.execute(
            "SELECT state FROM store_credential_states WHERE store_id=?",
            (store_id,),
        ).fetchone()

    assert "store_credential_candidates" in tables
    assert "store_credential_states" in tables
    assert state["state"] == "unknown"


def test_existing_store_database_migrates_credential_tables_and_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    initial = Database(path)
    initial.add_store("旧库店铺", app_id(1), secret(1))
    with initial.connect() as conn:
        conn.execute("DROP TRIGGER trg_store_credential_state_after_insert")
        conn.execute("DROP TABLE store_credential_candidates")
        conn.execute("DROP TABLE store_credential_states")

    migrated = Database(path)

    state = migrated.get_store_credential_state(1)
    backups = list(tmp_path.glob("legacy.db.pre-store-credential-recovery.bak"))
    assert state["state"] == "unknown"
    assert backups


def test_batch_preview_is_local_and_handles_thousand_rows(tmp_path: Path) -> None:
    database = Database(tmp_path / "stores.db")
    controller = NextUiController(
        database,
        product_service=CredentialService(),
    )
    lines = ["店铺名称,AppID,AppSecret,分组"]
    for index in range(100):
        lines.append(
            f"新店{index},{app_id(index + 10)},{secret(index + 10)},批量"
        )
    for index in range(900):
        source = index % 100
        lines.append(
            f"新店{source},{app_id(source + 10)},{secret(source + 10)},批量"
        )
    preview = controller.dispatch(
        "stores.batch.preview",
        preview_payload("\n".join(lines)),
    )
    committed = controller.dispatch(
        "stores.batch.commit",
        {
            **preview_payload("\n".join(lines)),
            "source_hash": preview["source_hash"],
                "background_refresh_enabled": False,
            "row_numbers": [
                row["row_number"]
                for row in preview["rows"]
                if row["status"] == "new"
            ],
        },
    )

    assert preview["network_requests"] == 0
    assert controller.product_service.calls == []
    assert preview["summary"]["new"] == 100
    assert preview["summary"]["duplicate"] == 900
    assert committed["created_count"] == 100
    assert len(database.list_stores()) == 100
    assert controller.dispatch("stores.list", {})["stores"][0][
        "credential_state"
    ] == "unknown"


def test_batch_preview_classifies_existing_status_and_name_conflicts(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    unknown_id = database.add_store("同名店", app_id(1), secret(1))
    valid_id = database.add_store("有效店", app_id(2), secret(2))
    invalid_id = database.add_store("失效店", app_id(3), secret(3))
    database.set_store_credential_state(valid_id, "valid", mark_probe=True)
    database.set_store_credential_state(
        invalid_id,
        "invalid",
        error_code="40125",
        error_message="invalid app secret",
        mark_probe=True,
    )
    with database.connect() as conn:
        protected_before = {
            row["id"]: row["secret_protected"]
            for row in conn.execute(
                "SELECT id, secret_protected FROM stores"
            ).fetchall()
        }
    controller = NextUiController(
        database,
        product_service=CredentialService(),
    )
    text = "\n".join(
        [
            "店铺名称,AppID,AppSecret,分组",
            f"同名店,{app_id(1)},{secret(99)},保留",
            f"有效店,{app_id(2)},{secret(98)},保留",
            f"失效店,{app_id(3)},{secret(97)},保留",
            f"同名店,{app_id(4)},{secret(4)},冲突",
            f",{app_id(5)},{secret(5)},错误",
            f"坏 AppID,not-an-appid,{secret(6)},错误",
            "控制字符,wx0000000000000009,abc\\x07def,错误",
            f"重复首条,{app_id(7)},{secret(7)},重复",
            f"重复二条,{app_id(7)},{secret(70)},重复",
        ]
    )
    preview = controller.dispatch(
        "stores.batch.preview",
        preview_payload(text),
    )
    rows = {row["row_number"]: row for row in preview["rows"]}

    assert rows[2]["status"] == "skip"
    assert rows[3]["status"] == "skip"
    assert rows[4]["status"] == "replace_candidate"
    assert rows[5]["status"] == "name_conflict"
    assert rows[6]["status"] == "invalid"
    assert rows[7]["status"] == "invalid"
    assert rows[8]["status"] == "invalid"
    assert rows[9]["status"] == "new"
    assert rows[10]["status"] == "duplicate"
    assert unknown_id != invalid_id
    assert all("secret" not in row for row in preview["rows"])
    assert "Secret-" not in json.dumps(preview, ensure_ascii=False)

    resolved_payload = {
        **preview_payload(text),
        "name_overrides": {"5": "同名新店"},
    }
    resolved = controller.dispatch(
        "stores.batch.preview",
        resolved_payload,
    )
    resolved_conflict = next(
        row for row in resolved["rows"] if row["row_number"] == 5
    )
    assert resolved_conflict["name"] == "同名新店"
    assert resolved_conflict["status"] == "new"
    committed = controller.dispatch(
        "stores.batch.commit",
        {
            **resolved_payload,
            "source_hash": resolved["source_hash"],
            "row_numbers": [4, 5, 9],
        },
    )
    assert committed["candidate_count"] == 1
    assert committed["created_count"] == 2
    candidate = database.list_store_credential_candidates(invalid_id)[0]
    assert candidate["state"] == "pending"
    assert len(database.list_stores()) == 5
    with database.connect() as conn:
        protected_after = {
            row["id"]: row["secret_protected"]
            for row in conn.execute(
                "SELECT id, secret_protected FROM stores WHERE id IN (?, ?, ?)",
                (unknown_id, valid_id, invalid_id),
            ).fetchall()
        }
    for store_id in (unknown_id, valid_id, invalid_id):
        assert protected_after[store_id] == protected_before[store_id]
    _store, old_secret = database.get_store_secret(invalid_id)
    assert old_secret == secret(3)


def test_store_batch_csv_gb18030_and_xlsx_parse(tmp_path: Path) -> None:
    database = Database(tmp_path / "stores.db")
    controller = NextUiController(database)
    csv_previews = []
    for index, encoding in enumerate(("utf-8", "utf-8-sig", "gb18030")):
        csv_text = (
            "店铺名称,AppID,AppSecret,分组\n"
            f"CSV店铺{index},{app_id(10 + index)},{secret(10 + index)},CSV组\n"
        )
        csv_previews.append(
            controller.dispatch(
                "stores.batch.preview",
                {
                    "source_kind": "csv",
                    "source_name": f"店铺-{encoding}.csv",
                    "content_base64": base64.b64encode(
                        csv_text.encode(encoding)
                    ).decode("ascii"),
                },
            )
        )
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["店铺名称", "AppID", "AppSecret", "分组"])
    sheet.append(["XLSX店铺", app_id(11), secret(11), "XLSX组"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    xlsx_preview = controller.dispatch(
        "stores.batch.preview",
        {
            "source_kind": "xlsx",
            "source_name": "店铺.xlsx",
            "content_base64": base64.b64encode(buffer.getvalue()).decode(
                "ascii"
            ),
        },
    )

    assert all(
        preview["rows"][0]["status"] == "new"
        for preview in csv_previews
    )
    assert [preview["rows"][0]["name"] for preview in csv_previews] == [
        "CSV店铺0",
        "CSV店铺1",
        "CSV店铺2",
    ]
    assert xlsx_preview["rows"][0]["status"] == "new"
    assert xlsx_preview["rows"][0]["name"] == "XLSX店铺"


def test_store_batch_accepts_alternating_app_id_and_secret_lines(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    controller = NextUiController(database)
    app_ids = [app_id(31), app_id(32), app_id(33)]
    secrets = [secret(31), secret(32), secret(33)]
    text = "\n".join(
        f"{value}\\"
        for pair in zip(app_ids, secrets, strict=True)
        for value in pair
    )

    preview = controller.dispatch(
        "stores.batch.preview",
        preview_payload(text),
    )

    assert preview["network_requests"] == 0
    assert preview["summary"]["new"] == 3
    assert [row["app_id"] for row in preview["rows"]] == app_ids
    assert [row["name"] for row in preview["rows"]] == app_ids
    assert all(row["status"] == "new" for row in preview["rows"])
    assert all(row["secret_masked"] == "••••••••" for row in preview["rows"])
    assert "Secret-" not in json.dumps(preview, ensure_ascii=False)

    committed = controller.dispatch(
        "stores.batch.commit",
        {
            **preview_payload(text),
            "source_hash": preview["source_hash"],
            "row_numbers": [row["row_number"] for row in preview["rows"]],
        },
    )

    assert committed["created_count"] == 3
    stores = database.list_stores()
    assert [store.app_id for store in stores] == app_ids
    assert [store.name for store in stores] == app_ids
    for store, expected_secret in zip(stores, secrets, strict=True):
        _store, stored_secret = database.get_store_secret(store.id)
        assert stored_secret == expected_secret


def test_batch_commit_starts_background_store_name_hydration(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    completed = threading.Event()

    class ProfileService(CredentialService):
        def sync_store_profile(self, store):
            database.replace_placeholder_store_name(
                store.id,
                store.app_id,
                "真实店铺名称",
            )
            completed.set()
            return type("Result", (), {"message": "店铺资料已更新"})()

    controller = NextUiController(
        database,
        product_service=ProfileService(),
    )
    text = f"{app_id(51)}\n{secret(51)}\\"
    preview = controller.dispatch(
        "stores.batch.preview",
        preview_payload(text),
    )

    committed = controller.dispatch(
        "stores.batch.commit",
        {
            **preview_payload(text),
            "source_hash": preview["source_hash"],
            "row_numbers": [row["row_number"] for row in preview["rows"]],
        },
    )

    assert committed["profile_hydration"]["started"] is True
    assert completed.wait(2)
    store = database.list_stores()[0]
    assert store.name == "真实店铺名称"
    for _ in range(100):
        if database.get_store_credential_state(store.id)["state"] == "valid":
            break
        time.sleep(0.01)
    assert database.get_store_credential_state(store.id)["state"] == "valid"


def test_store_batch_alternating_format_marks_missing_secret(
    tmp_path: Path,
) -> None:
    controller = NextUiController(Database(tmp_path / "stores.db"))
    text = f"{app_id(41)}\\\n{app_id(42)}\\"

    preview = controller.dispatch(
        "stores.batch.preview",
        preview_payload(text),
    )

    assert len(preview["rows"]) == 2
    assert preview["rows"][0]["app_id"] == app_id(41)
    assert preview["rows"][0]["status"] == "invalid"
    assert "AppSecret 为空" in preview["rows"][0]["reason"]
    assert preview["rows"][1]["app_id"] == app_id(42)
    assert preview["rows"][1]["status"] == "invalid"


def _business_counts(database: Database, store_id: int) -> tuple[int, int, int]:
    with database.connect() as conn:
        orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE store_id=?",
            (store_id,),
        ).fetchone()[0]
        products = conn.execute(
            "SELECT COUNT(*) FROM products WHERE store_id=?",
            (store_id,),
        ).fetchone()[0]
        after_sales = conn.execute(
            "SELECT COUNT(*) FROM after_sales WHERE store_id=?",
            (store_id,),
        ).fetchone()[0]
    return int(orders), int(products), int(after_sales)


def test_candidate_replacement_success_preserves_store_identity_and_business_data(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("恢复店", app_id(20), secret(20), "分组保留")
    database.upsert_product(
        Product(
            store_id=store_id,
            store_name="恢复店",
            product_id="P-1",
            title="商品",
            status=5,
            status_text="上架",
            edit_status=0,
            min_price=100,
            sku_count=1,
            stock_total=1,
            spu_code="",
            out_product_id="",
            head_img="",
            raw={},
        )
    )
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="恢复店",
            order_id="O-1",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_000,
            pay_time=1_700_000_000,
            total_amount=100,
            product_amount=100,
            freight_amount=0,
            discount_amount=0,
            buyer_name="买家",
            phone="",
            address="",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="P-1",
                    sku_id="S-1",
                    title="商品",
                    sku_count=1,
                    sale_price=100,
                )
            ],
        )
    )
    database.upsert_after_sale(
        store_id,
        {
            "after_sale_order_id": "A-1",
            "order_id": "O-1",
            "reason_text": "测试",
        },
    )
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        error_message="invalid app secret",
        mark_probe=True,
    )
    candidate = database.create_store_credential_candidate(
        store_id,
        secret(21),
        source_kind="paste",
        source_name="paste",
        source_row=2,
    )
    before_counts = _business_counts(database, store_id)
    service = CredentialService(
        {
            secret(20): WechatApiError("invalid appsecret", code=40125),
            secret(21): "连接成功",
        }
    )
    controller = NextUiController(database, product_service=service)

    refreshed_store, outcome = controller._refresh_store_with_credential(
        database.get_store(store_id),
        service,
    )

    assert outcome["action"] == "recovered"
    assert refreshed_store.id == store_id
    assert refreshed_store.name == "恢复店"
    assert refreshed_store.group_name == "分组保留"
    _store, new_secret = database.get_store_secret(store_id)
    assert new_secret == secret(21)
    assert database.get_store_credential_state(store_id)["state"] == "valid"
    applied = database.list_store_credential_candidates(store_id)[0]
    assert applied["id"] == candidate["id"]
    assert applied["state"] == "applied"
    assert _business_counts(database, store_id) == before_counts


def test_old_credential_recovery_supersedes_candidate(tmp_path: Path) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("原密钥恢复", app_id(30), secret(30))
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )
    candidate = database.create_store_credential_candidate(
        store_id,
        secret(31),
        source_kind="paste",
        source_name="paste",
        source_row=2,
    )
    service = CredentialService({secret(30): "连接成功"})
    controller = NextUiController(database, product_service=service)

    store, outcome = controller._refresh_store_with_credential(
        database.get_store(store_id),
        service,
    )

    assert outcome["action"] == "superseded"
    _store, current = database.get_store_secret(store_id)
    assert current == secret(30)
    assert database.get_store_credential_state(store_id)["state"] == "valid"
    assert database.list_store_credential_candidates(store_id)[0][
        "state"
    ] == "superseded"
    assert candidate["state"] == "pending"
    assert store.id == store_id


def test_candidate_invalid_and_transient_errors_keep_original_secret(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("双失效", app_id(40), secret(40))
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )
    invalid_candidate = database.create_store_credential_candidate(
        store_id,
        secret(41),
        source_kind="paste",
        source_name="paste",
        source_row=2,
    )
    invalid_service = CredentialService(
        {
            secret(40): WechatApiError("invalid", code=40125),
            secret(41): WechatApiError("invalid", code=40125),
        }
    )
    controller = NextUiController(
        database,
        product_service=invalid_service,
    )
    _store, rejected = controller._refresh_store_with_credential(
        database.get_store(store_id),
        invalid_service,
    )

    assert rejected["action"] == "rejected"
    assert database.list_store_credential_candidates(store_id)[0][
        "state"
    ] == "invalid"
    assert database.get_store_secret(store_id)[1] == secret(40)
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )

    transient_candidate = database.create_store_credential_candidate(
        store_id,
        secret(42),
        source_kind="paste",
        source_name="paste",
        source_row=3,
    )
    transient_service = CredentialService(
        {
            secret(40): WechatApiError("invalid", code=40125),
            secret(42): TimeoutError("network timeout"),
        }
    )
    controller = NextUiController(
        database,
        product_service=transient_service,
    )
    _store, deferred = controller._refresh_store_with_credential(
        database.get_store(store_id),
        transient_service,
    )

    assert deferred["action"] == "deferred"
    assert next(
        row
        for row in database.list_store_credential_candidates(store_id)
        if row["id"] == transient_candidate["id"]
    )["state"] == "pending"
    assert invalid_candidate["state"] == "pending"
    assert database.get_store_secret(store_id)[1] == secret(40)

    for index, transient_error in enumerate(
        (
            WechatApiError("rate limit", code=45011),
            WechatApiError("platform unavailable", code=50002),
        ),
        1,
    ):
        transient_store_id = database.add_store(
            f"临时错误店{index}",
            app_id(60 + index),
            secret(60 + index),
        )
        database.set_store_credential_state(
            transient_store_id,
            "invalid",
            error_code="40125",
            mark_probe=True,
        )
        candidate_secret = secret(50 + index)
        candidate = database.create_store_credential_candidate(
            transient_store_id,
            candidate_secret,
            source_kind="paste",
            source_name="paste",
            source_row=10 + index,
        )
        service = CredentialService(
            {
                secret(60 + index): WechatApiError(
                    "invalid",
                    code=40125,
                ),
                candidate_secret: transient_error,
            }
        )
        controller = NextUiController(
            database,
            product_service=service,
        )
        _store, outcome = controller._refresh_store_with_credential(
            database.get_store(transient_store_id),
            service,
        )
        assert outcome["action"] == "deferred"
        assert next(
            row
            for row in database.list_store_credential_candidates(
                transient_store_id
            )
            if row["id"] == candidate["id"]
        )["state"] == "pending"
        assert database.get_store_secret(transient_store_id)[1] == secret(
            60 + index
        )


def test_candidate_claim_is_single_winner_and_stale_validation_recovers(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("并发店", app_id(50), secret(50))
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )
    candidate = database.create_store_credential_candidate(
        store_id,
        secret(51),
        source_kind="paste",
        source_name="paste",
        source_row=2,
    )
    results = []

    def claim() -> None:
        results.append(
            database.claim_pending_credential_candidate(store_id)
        )

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    claimed = [row for row in results if row is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == candidate["id"]

    with database.connect() as conn:
        conn.execute(
            """
            UPDATE store_credential_candidates
            SET validating_started_at='2000-01-01T00:00:00+00:00'
            WHERE id=?
            """,
            (candidate["id"],),
        )
    recovered = database.claim_pending_credential_candidate(store_id)
    assert recovered["id"] == candidate["id"]
    assert recovered["state"] == "validating"


def test_refresh_credential_probe_only_marks_explicit_results(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    valid_id = database.add_store("有效探测", app_id(70), secret(70))
    invalid_id = database.add_store("失效探测", app_id(71), secret(71))
    transport_id = database.add_store("网络错误", app_id(72), secret(72))
    controller = NextUiController(database)

    controller._record_store_credential_probe(
        database.get_store(valid_id),
        {"_credential_probe_ok": True},
    )
    controller._record_store_credential_probe(
        database.get_store(invalid_id),
        {
            "_credential_error_code": "40125",
            "_credential_error_message": "invalid appsecret",
        },
    )
    controller._record_store_credential_probe(
        database.get_store(transport_id),
        {
            "_credential_checked": True,
            "_credential_probe_ok": False,
        },
    )

    assert database.get_store_credential_state(valid_id)["state"] == "valid"
    assert database.get_store_credential_state(invalid_id)["state"] == "invalid"
    assert database.get_store_credential_state(transport_id)["state"] == "unknown"


def test_credentials_detail_and_audit_never_return_plaintext_secret(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "stores.db")
    store_id = database.add_store("脱敏店", app_id(60), secret(60))
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )
    candidate = database.create_store_credential_candidate(
        store_id,
        secret(61),
        source_kind="paste",
        source_name="private.csv",
        source_row=2,
    )
    controller = NextUiController(database)
    detail = controller.dispatch(
        "stores.credentials.detail",
        {"store_id": store_id},
    )
    rendered = json.dumps(detail, ensure_ascii=False)

    assert secret(60) not in rendered
    assert secret(61) not in rendered
    assert len(detail["candidates"][0]["fingerprint_prefix"]) == 10
    with database.connect() as conn:
        protected = conn.execute(
            """
            SELECT secret_protected FROM store_credential_candidates
            WHERE id=?
            """,
            (candidate["id"],),
        ).fetchone()["secret_protected"]
    assert secret(61).encode("utf-8") not in protected
