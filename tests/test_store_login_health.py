from __future__ import annotations

from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


class ExpiredSession:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.calls = 0

    def probe(self, store):
        self.calls += 1
        detail = "微信后台要求重新登录"
        self.database.update_web_status(store.id, "已掉线", detail)
        return "已掉线", detail


class MetricsService:
    def __init__(self) -> None:
        self.calls = 0

    def refresh_store_metrics(self, store):
        self.calls += 1
        return {
            "gmv_today": 5300,
            "today_orders": 1,
            "_sales_attempt_status": {"today": "updated"},
        }


def test_store_refresh_probes_real_web_session_and_marks_expired(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "health.db")
    store_id = database.add_store("掉线店铺", "wx-expired", "secret")
    database.update_web_status(store_id, "已登录", "旧缓存状态")
    web_session = ExpiredSession(database)
    metrics = MetricsService()
    controller = NextUiController(
        database,
        web_session=web_session,
        product_service=metrics,
    )
    store = database.get_store(store_id)
    assert store is not None

    active, credential = controller._refresh_store_with_credential(
        store,
        metrics,
    )

    assert web_session.calls == 1
    assert metrics.calls == 1
    assert active.web_status == "已掉线"
    assert credential["web_status"] == "已掉线"
    listed = controller.dispatch("stores.list", {})["stores"][0]
    assert listed["web_status"] == "已掉线"
    assert listed["web_status_detail"] == "微信后台要求重新登录"


def test_store_health_probe_is_not_duplicated_for_same_refresh(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "health-once.db")
    store_id = database.add_store("正常店铺", "wx-healthy", "secret")
    web_session = ExpiredSession(database)
    metrics = MetricsService()
    controller = NextUiController(
        database,
        web_session=web_session,
        product_service=metrics,
    )
    controller._refresh_store_with_credential(
        database.get_store(store_id),
        metrics,
    )
    assert web_session.calls == 1


def test_store_health_probe_exception_is_visible_without_aborting_metrics_refresh(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "health-probe-error.db")
    store_id = database.add_store("探测异常店铺", "wx-probe-error", "secret")

    class BrokenSession:
        def probe(self, _store):
            raise OSError("network unavailable")

    metrics = MetricsService()
    controller = NextUiController(
        database,
        web_session=BrokenSession(),
        product_service=metrics,
    )
    store = database.get_store(store_id)
    assert store is not None

    active, credential = controller._refresh_store_with_credential(
        store,
        metrics,
    )

    assert metrics.calls == 1
    assert active.web_status == "检测异常"
    assert "network unavailable" in active.web_status_detail
    assert credential["web_status"] == "检测异常"
