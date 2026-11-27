from __future__ import annotations

import hashlib
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.big_screen import BigScreenAdapter
from weimi_manager.dashboard_metrics import build_dashboard_snapshot
from weimi_manager.db import Database
from weimi_manager.models import Order
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


ROOT = Path(__file__).parents[1]
REFERENCE_HASH = "2064FA42AEC8B64110ABF22A03C64E638B957F94376D34DE6CE3A9C87D67EB2C"
METRICS_HASH = "923EBA638F7A54165ACBC0BCC5544D67A5FDECD37B950A88036BE6C53F60CD9F"
SKELETONS_HASH = "1266C665845DA88B30FED5F56DABD90FE5B59B3B98AFBE5226676E84C965C535"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_legacy_big_screen_sources_remain_byte_identical() -> None:
    assert _sha256(ROOT / "weimi_manager/ui/reference_big_screen.py") == REFERENCE_HASH
    assert _sha256(ROOT / "weimi_manager/dashboard_metrics.py") == METRICS_HASH
    assert _sha256(ROOT / "weimi_manager/ui/skeletons.py") == SKELETONS_HASH


def test_big_screen_launcher_branch_precedes_startup_update_and_webui() -> None:
    launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
    branch = launcher.index('if "--big-screen" in sys.argv[1:]:')
    startup = launcher.index(
        "from weimi_manager.startup_update import run_startup_update"
    )
    webui = launcher.index("from weimi_manager.webui.main import main")
    assert branch < startup < webui
    assert "from weimi_manager.big_screen import main as big_screen_main" in launcher


def test_empty_big_screen_rankings_hide_tree_body_without_light_placeholder() -> None:
    from weimi_manager.ui.reference_big_screen import ReferenceBigScreenPage

    class PackedWidget:
        def __init__(self) -> None:
            self.packed = 0
            self.hidden = 0

        def winfo_manager(self):
            return "pack" if self.packed and not self.hidden else ""

        def pack(self, **_kwargs) -> None:
            self.packed += 1
            self.hidden = 0

        def pack_forget(self) -> None:
            self.hidden += 1

    class Tree(PackedWidget):
        def __init__(self) -> None:
            super().__init__()
            self.deleted = False
            self.rows = []

        def delete(self, *_items) -> None:
            self.deleted = True

        def get_children(self):
            return ()

        def insert(self, _parent, _index, *, values) -> None:
            self.rows.append(values)

    page = object.__new__(ReferenceBigScreenPage)
    tree = Tree()
    more_button = PackedWidget()
    more_button.packed = 1
    page.rank_trees = {"purchase": tree}
    page.rank_more_buttons = {"purchase": more_button}
    page._render_rank("purchase", [], False)
    assert tree.deleted is True
    assert tree.pack_forget.__self__ is tree
    assert tree.hidden == 1
    assert more_button.hidden == 1

    row = SimpleNamespace(label="采购账号 A", values=[3])
    page._render_rank("purchase", [row], False)
    assert tree.packed == 1
    assert tree.hidden == 0
    assert tree.rows == [["采购账号 A", "3"]]
    assert more_button.packed == 2
    assert more_button.hidden == 0


def test_controller_opens_one_big_screen_with_current_account_data(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "account-a" / "weimi_manager.db")
    controller = NextUiController(database, project_dir=tmp_path)
    process = SimpleNamespace(pid=43210, poll=lambda: None)

    with mock.patch(
        "weimi_manager.webui.controller.subprocess.Popen",
        return_value=process,
    ) as popen:
        first = controller.dispatch("dashboard.big-screen.open", {})
        second = controller.dispatch("dashboard.big-screen.open", {})

    assert first["opened"] is True
    assert first["pid"] == 43210
    assert Path(first["data_dir"]) == database.path.parent.resolve()
    assert second["already_running"] is True
    assert popen.call_count == 1
    command = popen.call_args.args[0]
    assert "-m" in command
    assert "weimi_manager.big_screen" in command
    assert "--parent-pid" in command
    assert "--data-dir" in command
    assert popen.call_args.kwargs["env"]["WEIMI_DATA_DIR"] == str(
        database.path.parent.resolve()
    )


def test_big_screen_adapter_marshals_progress_and_completion_to_ui(
    tmp_path: Path,
) -> None:
    class RootStub:
        def __init__(self) -> None:
            self.callbacks: queue.Queue = queue.Queue()

        def after(self, _milliseconds, callback):
            self.callbacks.put(callback)

        def winfo_exists(self):
            return 1

    root = RootStub()
    database = Database(tmp_path / "adapter.db")
    adapter = BigScreenAdapter(root, database)
    progress = []
    completed = threading.Event()

    def work(report):
        report("同步店铺", 1, 3)
        return "ok"

    assert adapter.run_task(
        work,
        done=lambda value: (progress.append(value), completed.set()),
        progress=lambda message, current, total: progress.append(
            (message, current, total)
        ),
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            callback = root.callbacks.get_nowait()
        except queue.Empty:
            time.sleep(0.01)
            continue
        callback()
        if completed.is_set():
            break
    assert ("同步店铺", 1, 3) in progress
    assert "ok" in progress


def test_dashboard_snapshot_keeps_unclaimed_gift_out_of_sales(tmp_path: Path) -> None:
    database = Database(tmp_path / "dashboard.db")
    store_id = database.add_store("大屏店铺", "wx-dashboard", "secret")
    now = int(time.time())
    common = dict(
        store_id=store_id,
        store_name="大屏店铺",
        status_text="",
        update_time=now,
        pay_time=now,
        product_amount=0,
        freight_amount=0,
        discount_amount=0,
        buyer_name="",
        phone="",
        address="",
        waybill_id="",
        carrier="",
        merchant_notes="",
    )
    database.upsert_order(
        Order(
            order_id="GIFT",
            status=12,
            create_time=now,
            total_amount=50000,
            raw={"is_present": True},
            **common,
        )
    )
    database.upsert_order(
        Order(
            order_id="SALE",
            status=20,
            create_time=now,
            total_amount=12000,
            raw={"is_present": True},
            **common,
        )
    )

    snapshot = build_dashboard_snapshot(database, "今日")

    assert snapshot.store_orders == 1
    assert snapshot.store_receipt == 12000


def test_after_sale_sync_chunks_treats_thirty_day_cache_window(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "aftersale-chunks.db")
    store_id = database.add_store("售后店铺", "wx-aftersale-chunks", "secret")
    store = database.get_store(store_id)
    assert store is not None
    calls = []

    class Client:
        def fetch_after_sale_ids(
            self,
            start_time,
            end_time,
            *,
            time_field="create",
        ):
            calls.append((start_time, end_time, time_field))
            return []

    service = SyncService(database)
    now = int(time.time())
    with mock.patch.object(service, "client_for", return_value=Client()):
        result = service.sync_after_sales(
            store,
            now - 30 * 86400,
            now,
            time_field="create",
        )

    assert result.count == 0
    assert len(calls) == 30
    assert all(end - start <= 86400 for start, end, _field in calls)


def test_formal_specs_and_release_verifier_include_big_screen_assets() -> None:
    for spec_name in ("LangLang.spec", "LangLangWebUI.spec"):
        source = (ROOT / spec_name).read_text(encoding="utf-8")
        for module in (
            "weimi_manager.big_screen",
            "weimi_manager.dashboard_metrics",
            "weimi_manager.ui.reference_big_screen",
            "weimi_manager.ui.skeletons",
        ):
            assert module in source
    verifier = (ROOT / "tools/generate_stable_release.py").read_text(
        encoding="utf-8"
    )
    assert "正式包缺少数据大屏模块" in verifier
    assert "_tcl_data" in verifier
    assert "_tk_data" in verifier
