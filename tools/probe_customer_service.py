from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


PROFILE = Path(__file__).resolve().parent.parent / "data" / "edge_profiles" / "store_1"


def main() -> None:
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        str(PROFILE),
        channel="msedge",
        headless=True,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(
        "https://store.weixin.qq.com/shop/home",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(5000)
    items = page.eval_on_selector_all(
        "a",
        """els => els.map(e => ({
            text: (e.innerText || e.textContent || '').trim(),
            href: e.href
        })).filter(x => /客服|消息/.test(x.text) || /kf|customer|message/i.test(x.href))""",
    )
    print("URL", page.url)
    print("TITLE", page.title())
    print("TEXT", page.locator("body").inner_text(timeout=5000)[:4000])
    print(items[:100])
    context.close()
    playwright.stop()


if __name__ == "__main__":
    main()
