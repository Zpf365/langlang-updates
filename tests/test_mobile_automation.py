from __future__ import annotations

from weimi_manager.mobile_automation import MobileWechatAutomation, OcrLine


def line(text: str, x: int, y: int) -> OcrLine:
    return OcrLine(
        text=text,
        score=0.99,
        box=((x, y), (x + 120, y), (x + 120, y + 40), (x, y + 40)),
    )


def test_ocr_find_requires_exact_chat_title_or_matching_action_text() -> None:
    automation = object.__new__(MobileWechatAutomation)
    rows = [
        line("文件传输助手", 200, 150),
        line("搜索文件传输助手", 130, 500),
        line("发送", 1200, 1800),
    ]

    assert automation._find(rows, "文件传输助手", exact=True) == rows[0]
    assert automation._find(rows, "发送", exact=True) == rows[2]
    assert automation._find(rows, "加入购物车") is None


def test_ocr_find_can_require_a_result_below_the_search_box() -> None:
    automation = object.__new__(MobileWechatAutomation)
    rows = [
        line("文件传输助手", 200, 180),
        line("文件传输助手", 220, 530),
    ]

    match = automation._find(rows, "文件传输助手", min_y=400)

    assert match == rows[1]


def test_queue_runs_each_link_once_and_advances_phone_task() -> None:
    events: list[str] = []

    class FakeAdb:
        def unlocked(self):
            return True

        def devices(self):
            return ["device-1"]

        def launch_wechat(self):
            events.append("launch")

        def complete_and_next(self):
            events.append("next")

        def set_clipboard(self, value):
            events.append(f"clipboard:{value}")

    class FakeAutomation(MobileWechatAutomation):
        def __init__(self):
            self.adb = FakeAdb()
            self.progress = lambda _message: None

        def _open_file_transfer_assistant(self):
            events.append("file-helper")

        def _send_link(self, link):
            events.append(f"send:{link}")

        def _open_product(self):
            events.append("product")

        def _add_to_cart(self, quantity):
            events.append(f"cart:{quantity}")

        def _return_to_file_transfer_assistant(self):
            events.append("return")

    automation = FakeAutomation()
    result = automation.run_queue(
        ["#微信小店://店铺/one", "#微信小店://店铺/two"],
        quantity=3,
    )

    assert result == {"completed": 2, "total": 2}
    assert events == [
        "launch",
        "file-helper",
        "send:#微信小店://店铺/one",
        "product",
        "cart:3",
        "return",
        "next",
        "clipboard:#微信小店://店铺/two",
        "send:#微信小店://店铺/two",
        "product",
        "cart:3",
        "return",
        "next",
    ]
