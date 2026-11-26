from __future__ import annotations

import json
import threading
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from weimi_manager.service import SyncService, is_explicit_violation_record
from weimi_manager.ui.app import MainWindow


def test_explicit_violation_filter_keeps_actionable_titles() -> None:
    records = [
        {
            "title": "明确违规标题",
            "product_id": "1",
            "reason": "禁用词",
            "source": "违规记录",
            "status": "待处理",
        },
        {
            "title": "价格错误商品",
            "product_id": "2",
            "reason": "价格异常",
            "source": "违规记录",
            "status": "待处理",
        },
        {
            "title": "已撤销商品",
            "product_id": "3",
            "reason": "禁用词",
            "source": "违规记录",
            "status": "已撤销",
        },
        {
            "title": "普通预警",
            "product_id": "4",
            "reason": "店铺预警",
            "source": "预警",
            "status": "待处理",
        },
    ]

    kept = [
        record
        for record in records
        if is_explicit_violation_record(record)
    ]

    assert [record["product_id"] for record in kept] == ["1"]


def _daily_window() -> tuple[MainWindow, SimpleNamespace]:
    page = object.__new__(MainWindow)
    stores = [
        SimpleNamespace(
            id=1,
            name="店铺一",
            app_id="wx-one",
            web_status="已登录",
        ),
        SimpleNamespace(
            id=2,
            name="店铺二",
            app_id="wx-two",
            web_status="已登录",
        ),
    ]
    settings: dict[str, str] = {}
    synced: list[int] = []
    done = threading.Event()

    class Db:
        @staticmethod
        def list_stores():
            return stores

        @staticmethod
        def get_setting(key, default=""):
            return settings.get(key, default)

        @staticmethod
        def set_setting(key, value):
            settings[key] = value

        @staticmethod
        def start_task_log(**_kwargs):
            return 1

    class Service:
        @staticmethod
        def sync_violation_library(store):
            synced.append(store.id)

    page._closing = False
    page._violation_daily_running = False
    page._task_running = False
    page._account_sync_running = False
    page._auto_refresh_running = False
    page._pages = {}
    page.db = Db()
    page.service = Service()
    page.winfo_exists = lambda: False
    page._finish_task_log = lambda *_args, **_kwargs: None
    page._refresh_nav_alerts = lambda: None
    page._violation_daily_finished = (
        lambda *args: MainWindow._violation_daily_finished(page, *args)
    )

    def post(callback, *args):
        callback(*args)
        done.set()

    page._post = post
    return page, SimpleNamespace(
        settings=settings,
        synced=synced,
        done=done,
    )


def test_daily_sync_waits_until_idle_and_runs_all_logged_in_stores() -> None:
    page, state = _daily_window()
    page._task_running = True
    page._violation_daily_tick()
    assert state.synced == []

    page._task_running = False
    page._violation_daily_tick()
    assert state.done.wait(2)
    assert state.synced == [1, 2]
    assert (
        state.settings["violation_daily_last_success"]
        == date.today().isoformat()
    )


def test_daily_sync_success_marker_prevents_second_run() -> None:
    page, state = _daily_window()
    state.settings["violation_daily_last_success"] = date.today().isoformat()
    page._violation_daily_tick()
    time.sleep(0.05)
    assert state.synced == []


def test_store_sync_filters_non_actionable_records_and_uses_app_id() -> None:
    settings = {
        "violation_library": json.dumps(
            [
                {
                    "record_id": "old",
                    "store_id": 99,
                    "store_app_id": "wx-store",
                    "title": "旧记录",
                },
                {
                    "record_id": "keep",
                    "store_id": 7,
                    "title": "其他店铺记录",
                },
            ],
            ensure_ascii=False,
        ),
        "violation_blacklist": "[]",
    }

    class Db:
        @staticmethod
        def get_setting(key, default=""):
            return settings.get(key, default)

        @staticmethod
        def set_setting(key, value):
            settings[key] = value

    class Client:
        def __init__(self, _db, _store):
            pass

        @staticmethod
        def platform_violation_records():
            return [
                {
                    "record_id": "explicit-1",
                    "product_id": "1",
                    "title": "明确违规标题",
                    "reason": "禁用词",
                    "source": "违规记录",
                    "status": "待处理",
                },
                {
                    "record_id": "price-2",
                    "product_id": "2",
                    "title": "价格错误",
                    "reason": "价格异常",
                    "source": "违规记录",
                    "status": "待处理",
                },
            ]

    store = SimpleNamespace(
        id=9,
        name="店铺",
        app_id="wx-store",
    )
    with patch("weimi_manager.service.ShopPrivateClient", Client):
        result = SyncService(Db()).sync_violation_library(store)

    rows = json.loads(settings["violation_library"])
    assert result.success == 1
    assert [row["record_id"] for row in rows] == ["keep", "explicit-1"]
    assert rows[-1]["store_app_id"] == "wx-store"


def test_counterfeit_violation_titles_are_published_to_upload_filter() -> None:
    settings: dict[str, str] = {}

    class Db:
        @staticmethod
        def get_setting(key, default=""):
            return settings.get(key, default)

        @staticmethod
        def set_setting(key, value):
            settings[key] = value

        @staticmethod
        def get_product(_store_id, _product_id):
            return None

    class Client:
        def __init__(self, _db, _store):
            pass

        @staticmethod
        def platform_violation_records():
            return [
                {
                    "record_id": "fake-1",
                    "product_id": "10001",
                    "title": "毛绒花束捧花｜520生日礼物",
                    "reason": "出售假冒商品",
                    "source": "违规记录",
                    "status": "已处理",
                    "head_img": "https://example.test/fake.jpg",
                }
            ]

    store = SimpleNamespace(id=9, name="店铺", app_id="wx-store")
    with patch("weimi_manager.service.ShopPrivateClient", Client):
        SyncService(Db()).sync_violation_library(store)

    titles = json.loads(settings["upload_violation_titles"])
    assert titles[0]["title"] == "毛绒花束捧花｜520生日礼物"
    assert titles[0]["normalized"] == "毛绒花束捧花|520生日礼物"
    assert titles[0]["reason"] == "出售假冒商品"
