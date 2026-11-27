from __future__ import annotations

from weimi_manager.models import UploadPreflightError
from weimi_manager.ui.upload_page import _definitive_upload_failure_message
from weimi_manager.wechat_api import WechatApiError


def test_local_preflight_failure_is_reported_as_not_created() -> None:
    message = _definitive_upload_failure_message(
        UploadPreflightError("面料材质无法自动补全")
    )

    assert message == "商品未创建：面料材质无法自动补全"


def test_remote_unknown_failure_stays_pending() -> None:
    assert (
        _definitive_upload_failure_message(
            WechatApiError("系统繁忙", code=None)
        )
        == ""
    )


def test_unstructured_failure_stays_pending() -> None:
    assert _definitive_upload_failure_message(RuntimeError("网络中断")) == ""
