from __future__ import annotations

import pytest

from weimi_manager.procurement import (
    _attempt_1688_temporary_address,
    _temporary_address_payload,
)


ADDRESS = {
    "user_name": "测试收件人",
    "tel_number": "13800138000-6155",
    "province_name": "测试省",
    "city_name": "测试市",
    "county_name": "测试区",
    "street_name": "测试街道",
    "detail_info": "虚拟仓库一号门",
}


CHECKOUT_FIXTURE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    body { font: 16px sans-serif; margin: 24px; }
    button { margin: 8px; padding: 8px 16px; }
    .modal { display: none; position: fixed; inset: 80px 18%; background: white;
             border: 1px solid #ccc; padding: 24px; }
    .modal.open { display: block; }
    .field { margin: 18px 0; }
    textarea { width: 90%; height: 96px; }
    input { width: 70%; padding: 8px; }
  </style>
</head>
<body>
  <section id="current-address">旧收件地址</section>
  <button id="modify-address">修改地址</button>
  <button id="edit-temporary">编辑临时地址</button>

  <section class="modal" id="address-modal" role="dialog" aria-label="编辑临时地址">
    <h2>编辑临时地址</h2>
    <label>粘贴地址<textarea id="bulk-address"></textarea></label>
    <button id="auto-fill">自动填写</button>
    <div class="field"><label for="recipient-name">姓名</label>
      <input id="recipient-name"></div>
    <div class="field"><label>联系方式</label><input id="phone" value=""></div>
    <div class="field"><label>地区</label><input id="region" value=""></div>
    <div class="field"><label>邮编</label><input id="postal-code" value=""></div>
    <div class="field"><label for="recipient-address">地址</label>
      <input id="recipient-address"></div>
    <button id="save-address">保存</button>
    <button id="cancel-address">取消</button>
  </section>

  <script>
    window.__counters = {opened: 0, parsed: 0, saved: 0};
    const modal = document.querySelector('#address-modal');
    document.querySelector('#modify-address').onclick = () => {};
    document.querySelector('#edit-temporary').onclick = () => {
      window.__counters.opened += 1;
      modal.classList.add('open');
    };
    document.querySelector('#auto-fill').onclick = () => {
      window.__counters.parsed += 1;
      const lines = document.querySelector('#bulk-address').value
        .split(/\n+/).map((value) => value.trim()).filter(Boolean);
      const phoneLine = lines.find((value) => /^\d{11}$/.test(value)) || '';
      const addressLine = lines[lines.length - 1] || '';
      document.querySelector('#phone').value = phoneLine;
      document.querySelector('#region').value = '测试省 测试市 测试区';
      document.querySelector('#postal-code').value = '462300';
      document.querySelector('#recipient-address').value = addressLine;
    };
    document.querySelector('#save-address').onclick = () => {
      window.__counters.saved += 1;
      const name = document.querySelector('#recipient-name').value;
      const phone = document.querySelector('#phone').value;
      const address = document.querySelector('#recipient-address').value;
      document.querySelector('#current-address').textContent =
        `${address}\n${name}\n${phone}`;
      modal.classList.remove('open');
    };
    document.querySelector('#cancel-address').onclick = () => {
      modal.classList.remove('open');
    };
  </script>
</body>
</html>
"""


@pytest.fixture()
def browser_page():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=True,
            )
        page = browser.new_page()
        page.route(
            "**/order/smart_make_order.htm",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=CHECKOUT_FIXTURE,
            ),
        )
        page.goto(
            "https://order.1688.com/order/smart_make_order.htm",
            wait_until="domcontentloaded",
        )
        yield page
        browser.close()


def test_temporary_address_payload_avoids_duplicate_extension() -> None:
    payload = _temporary_address_payload(
        {
            **ADDRESS,
            "user_name": "测试收件人[6155]",
            "detail_info": "虚拟仓库一号门[6155]",
        }
    )

    assert payload["display_name"] == "测试收件人[6155]"
    assert payload["display_address"].endswith("虚拟仓库一号门[6155]")
    assert payload["display_address"].count("[6155]") == 1


def test_1688_temporary_address_is_filled_saved_and_read_back(
    browser_page,
) -> None:
    message = _attempt_1688_temporary_address(browser_page, ADDRESS)

    assert "已保存并回读一致" in message
    assert browser_page.locator("#recipient-name").input_value() == "测试收件人[6155]"
    assert browser_page.locator("#phone").input_value() == "13800138000"
    assert browser_page.locator("#recipient-address").input_value() == (
        "测试省测试市测试区测试街道虚拟仓库一号门[6155]"
    )
    assert browser_page.evaluate("window.__counters") == {
        "opened": 1,
        "parsed": 1,
        "saved": 1,
    }

    repeated = _attempt_1688_temporary_address(browser_page, ADDRESS)

    assert "已使用当前订单" in repeated
    assert browser_page.evaluate("window.__counters") == {
        "opened": 1,
        "parsed": 1,
        "saved": 1,
    }


def test_1688_temporary_address_missing_entry_does_not_claim_success(
    browser_page,
) -> None:
    browser_page.locator("#edit-temporary").evaluate(
        "(el) => el.remove()"
    )

    message = _attempt_1688_temporary_address(browser_page, ADDRESS)

    assert "人工处理" in message
    assert browser_page.evaluate("window.__counters")["saved"] == 0


def test_1688_temporary_address_requires_read_back(browser_page) -> None:
    browser_page.evaluate(
        """() => {
          document.querySelector('#save-address').onclick = () => {
            window.__counters.saved += 1;
            document.querySelector('#address-modal').classList.remove('open');
          };
        }"""
    )

    message = _attempt_1688_temporary_address(browser_page, ADDRESS)
    repeated = _attempt_1688_temporary_address(browser_page, ADDRESS)

    assert message == "1688临时地址保存结果未回读，请在结算页人工确认"
    assert "请勿重复提交" in repeated
    assert browser_page.evaluate("window.__counters")["saved"] == 1


def test_checkout_readback_ignores_text_from_procurement_sidebar(
    browser_page,
) -> None:
    payload = _temporary_address_payload(ADDRESS)
    browser_page.evaluate(
        """(text) => {
          const sidebar = document.createElement('aside');
          sidebar.id = 'langlang-procurement-sidebar';
          sidebar.textContent = text;
          document.body.append(sidebar);
        }""",
        " ".join(
            (
                payload["display_address"],
                payload["display_name"],
                payload["phone_base"],
            )
        ),
    )

    message = _attempt_1688_temporary_address(browser_page, ADDRESS)

    assert "已保存并回读一致" in message
    assert browser_page.evaluate("window.__counters")["saved"] == 1
