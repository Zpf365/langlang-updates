from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


def _template_payload(name: str, **changes):
    payload = {
        "name": name,
        "title_prefix": "澜琅",
        "title_suffix": "现货",
        "price_multiplier": 1.25,
        "price_offset": 320,
        "fixed_price_enabled": True,
        "fixed_price": 9990,
        "final_price_decimals": 2,
        "ladder_enabled": True,
        "ladder_rules": "0=49.99=1.2=3\n49.99=99.99=1.15=0",
        "stock": 1000,
        "listing_mode": "warehouse",
        "duplicate_filter_mode": "computer",
        "video_folder": r"C:\videos",
        "main_image_video": True,
        "use_package_tags": True,
        "read_packaging_labels": True,
        "clothing_mode": True,
        "seven_day_return": True,
        "shipping_insurance": True,
        "damage_return": True,
        "fake_one_three": True,
        "support_exchange": True,
        "auto_open_category": True,
        "force_fallback_category": True,
        "distributor_name": "测试经销者",
        "distributor_contact": "13800000000",
        "delivery_type": "现货",
        "delivery_time": "7天内发货",
        "title_replace_rules": "旧=新",
        "fallback_category": "测试类目",
        "deposit_limit": 300,
        "category_brand_ids": "brand-1",
        "category_license_groups": '[{"name":"营业执照"}]',
        "low_price_enabled": True,
        "low_price_multiplier": 2.5,
        "low_price_action": "删除最低价",
        "trim_head": 1,
        "trim_detail": 2,
        "cloud_violation": True,
        "cloud_violation_types": "出售假冒商品\n质量不合格",
        "strip_title_brackets": True,
        "title_rules": "禁词",
        "sku_rules": "禁用规格",
        "image_mode": "OCR 识图",
        "head_action": "跳过商品",
        "detail_action": "删除违规图片",
        "image_rules": "水印",
        "enable_custom_attributes": True,
        "attribute_rules": "材质=棉",
        "future_field": "must-survive",
    }
    payload.update(changes)
    return payload


def test_upload_template_round_trip_copy_default_and_delete(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    saved = controller.dispatch(
        "upload.templates.save",
        {"template": _template_payload("完整模板")},
    )["template"]
    template_id = saved["id"]

    reloaded = NextUiController(database).dispatch("upload.list", {})
    row = reloaded["templates"][0]
    for key, value in _template_payload("完整模板").items():
        assert row[key] == value
    assert row["price_offset"] == 320
    assert row["fixed_price"] == 9990
    assert row["future_field"] == "must-survive"

    second = controller.dispatch(
        "upload.templates.save",
        {"template": _template_payload("第二模板")},
    )["template"]
    controller.dispatch(
        "upload.templates.set-default",
        {"template_id": second["id"]},
    )
    copied = controller.dispatch(
        "upload.templates.copy",
        {"template_id": template_id},
    )["template"]
    assert copied["name"] == "完整模板 副本"
    assert copied["id"] != template_id
    assert copied["ladder_rules"] == "0=49.99=1.2=3\n49.99=99.99=1.15=0"
    assert copied["category_license_groups"] == '[{"name":"营业执照"}]'
    assert copied["future_field"] == "must-survive"
    assert copied["is_default"] is False

    after_restart = NextUiController(database).dispatch(
        "upload.templates.defaults",
        {},
    )
    assert after_restart["default_template_id"] == second["id"]
    assert after_restart["default_template"]["name"] == "第二模板"

    controller.dispatch(
        "upload.templates.delete",
        {"template_id": second["id"]},
    )
    after_delete = NextUiController(database).dispatch(
        "upload.templates.defaults",
        {},
    )
    assert after_delete["default_template_id"] == ""


def test_upload_template_validation_and_real_cloud_reasons(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    database.set_setting(
        "violation_library",
        json.dumps(
            [
                {"reason": "出售假冒商品"},
                {"reason": "质量不合格"},
                {"reason": "出售假冒商品"},
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)

    with pytest.raises(ValueError, match="阶梯倍率"):
        controller.dispatch(
            "upload.templates.save",
            {"template": _template_payload("错误阶梯", ladder_rules="1=2=3")},
        )
    with pytest.raises(ValueError, match="证照组"):
        controller.dispatch(
            "upload.templates.save",
            {
                "template": _template_payload(
                    "错误证照",
                    category_license_groups='{"name":"营业执照"}',
                )
            },
        )
    listed = controller.dispatch("upload.list", {})
    assert listed["cloud_violation_types"] == ["出售假冒商品", "质量不合格"]


def test_default_template_is_used_for_task_snapshot(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("模板任务店铺", "wx-template-task", "secret")
    controller = NextUiController(database)
    template = controller.dispatch(
        "upload.templates.save",
        {"template": _template_payload("默认任务模板")},
    )["template"]
    controller.dispatch(
        "upload.templates.set-default",
        {"template_id": template["id"]},
    )
    imported = controller.dispatch(
        "upload.batch.import",
        {
            "append": False,
            "files": [
                {
                    "name": "task.json",
                    "content": json.dumps(
                        {
                            "platform": "淘宝",
                            "productId": "TB-TEMPLATE-1",
                            "productName": "模板快照商品",
                            "price": 1000,
                            "stock": 8,
                            "itemImgs": ["https://img.alicdn.com/main.jpg"],
                            "skus": [
                                {"price": "1000", "quantity": "8", "id": "1"}
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
    )
    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": [imported["batch"][0]["batch_id"]],
            "store_ids": [store_id],
        },
    )
    snapshot = created["tasks"][0]["template"]
    assert snapshot["name"] == "默认任务模板"
    assert snapshot["attribute_rules"] == "材质=棉"
    assert snapshot["future_field"] == "must-survive"
    assert snapshot["category_business_type"] == "individual"

    changed = _template_payload("修改后的模板")
    changed["id"] = template["id"]
    controller.dispatch("upload.templates.save", {"template": changed})
    persisted = controller.dispatch("upload.list", {})["tasks"][0]["template"]
    assert persisted["name"] == "默认任务模板"


def test_native_template_folder_picker_returns_path_or_cancel(tmp_path: Path) -> None:
    controller = NextUiController(Database(tmp_path / "webui.db"))
    completed = mock.Mock(returncode=0, stdout=r"C:\videos" + "\n", stderr="")
    with mock.patch(
        "weimi_manager.webui.controller.subprocess.run",
        return_value=completed,
    ) as run:
        result = controller.dispatch(
            "upload.templates.select-folder",
            {"initial_path": r"C:\old-videos"},
        )
    assert result["path"] == r"C:\videos"
    assert run.call_args.kwargs["env"]["LANG_UPLOAD_FOLDER_INITIAL"] == r"C:\old-videos"

    cancelled = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch(
        "weimi_manager.webui.controller.subprocess.run",
        return_value=cancelled,
    ):
        assert controller.dispatch(
            "upload.templates.select-folder",
            {},
        )["path"] == ""
