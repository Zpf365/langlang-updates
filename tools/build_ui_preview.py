from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weimi_manager.config import AppPaths  # noqa: E402
from weimi_manager.service import SyncService  # noqa: E402
from weimi_manager.ui.app import MainWindow  # noqa: E402

OUTPUT = ROOT / "assets" / "ui-framework-preview.png"

PAGES = [
    ("workbench", "工作台"),
    ("stores", "店铺管理"),
    ("orders", "订单管理"),
    ("products", "商品管理"),
    ("operations", "运营中心"),
    ("customer_service", "客服工作台"),
    ("marketing", "营销中心"),
    ("storefront", "店铺主页"),
    ("finance", "资金账户"),
    ("procurement", "采购中心"),
    ("analytics", "数据中心"),
    ("automation", "自动化"),
    ("settings", "设置"),
    ("account", "账号中心"),
]


def seed_demo() -> None:
    from weimi_manager.db import Database

    paths = AppPaths.discover()
    db = Database(paths.database)
    store_id = db.add_store("澜琅演示店铺", "demo-store", "local-demo", "演示分组")
    store = db.get_store(store_id)
    assert store
    service = SyncService(db)
    end_time = int(time.time())
    service.sync_orders(store, end_time - 6 * 86400, end_time)
    service.sync_products(store)
    service.refresh_shipping_options(store)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="langlang_preview_") as temp:
        os.environ["WEIMI_DATA_DIR"] = temp
        os.environ["LANGLANG_START_PAGE"] = "workbench"
        seed_demo()
        window = MainWindow(AppPaths.discover())
        window.geometry("1400x830+80+80")
        window.update()
        screens: list[tuple[str, Image.Image]] = []
        for key, label in PAGES:
            window.show_page(key)
            window.lift()
            window.update_idletasks()
            window.update()
            time.sleep(0.12)
            x = window.winfo_rootx()
            y = window.winfo_rooty()
            width = window.winfo_width()
            height = window.winfo_height()
            image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
            screens.append((label, image))
        window.destroy()

    columns = 3
    tile_width = 560
    gap = 18
    label_height = 38
    rows = (len(screens) + columns - 1) // columns
    sample = screens[0][1]
    tile_height = round(sample.height * tile_width / sample.width)
    sheet = Image.new(
        "RGB",
        (
            columns * tile_width + (columns + 1) * gap,
            rows * (tile_height + label_height) + (rows + 1) * gap,
        ),
        "#e9e4d8",
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(screens):
        row, column = divmod(index, columns)
        x = gap + column * (tile_width + gap)
        y = gap + row * (tile_height + label_height + gap)
        thumbnail = image.resize((tile_width, tile_height), Image.Resampling.LANCZOS)
        sheet.paste(thumbnail, (x, y))
        draw.text((x + 4, y + tile_height + 8), label, fill="#173638")
    sheet.save(OUTPUT, quality=95)
    print(f"Saved UI preview: {OUTPUT}")


if __name__ == "__main__":
    main()
