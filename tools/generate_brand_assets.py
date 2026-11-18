from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS = ASSETS / "icons"
SCALE = 4
SIZE = 64
INK = "#0b292d"
ACTIVE = "#f8fffc"
MUTED = "#91aaa7"
TEAL = "#23b7a6"
GOLD = "#d7b66a"


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def p(value: float) -> int:
    return round(value * SCALE)


def line(draw: ImageDraw.ImageDraw, points, fill: str, width: float = 3.5):
    scaled = [(p(x), p(y)) for x, y in points]
    draw.line(scaled, fill=fill, width=p(width), joint="curve")
    radius = p(width / 2)
    for x, y in scaled:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def rounded(draw, box, radius, outline, width=3.5, fill=None):
    draw.rounded_rectangle(
        tuple(p(value) for value in box),
        radius=p(radius),
        outline=outline,
        width=p(width),
        fill=fill,
    )


def draw_home(draw, color):
    rounded(draw, (10, 10, 29, 26), 4, color)
    rounded(draw, (34, 10, 54, 34), 4, color)
    rounded(draw, (10, 31, 29, 54), 4, color)
    rounded(draw, (34, 39, 54, 54), 4, color)


def draw_store(draw, color):
    line(draw, [(10, 26), (16, 11), (48, 11), (54, 26)], color)
    line(draw, [(10, 26), (54, 26), (54, 52), (10, 52), (10, 26)], color)
    line(draw, [(10, 29), (18, 35), (26, 29), (34, 35), (42, 29), (54, 35)], color, 2.5)
    rounded(draw, (25, 39, 39, 52), 2, color, 2.5)


def draw_orders(draw, color):
    line(draw, [(16, 9), (48, 9), (48, 54), (42, 50), (36, 54), (30, 50), (24, 54), (16, 50), (16, 9)], color)
    line(draw, [(23, 23), (41, 23)], color, 2.6)
    line(draw, [(23, 31), (41, 31)], color, 2.6)
    line(draw, [(23, 39), (35, 39)], color, 2.6)


def draw_shipping(draw, color):
    rounded(draw, (8, 20, 36, 46), 4, color)
    line(draw, [(36, 29), (47, 29), (55, 37), (55, 46), (36, 46)], color)
    draw.ellipse((p(16), p(43), p(27), p(54)), outline=color, width=p(3.2))
    draw.ellipse((p(42), p(43), p(53), p(54)), outline=color, width=p(3.2))
    line(draw, [(12, 20), (18, 13), (30, 13), (36, 20)], GOLD if color != MUTED else color, 2.5)


def draw_products(draw, color):
    line(draw, [(32, 9), (53, 20), (53, 43), (32, 55), (11, 43), (11, 20), (32, 9)], color)
    line(draw, [(11, 20), (32, 32), (53, 20)], color)
    line(draw, [(32, 32), (32, 55)], color)
    line(draw, [(21, 15), (43, 27)], color, 2.5)


def draw_operations(draw, color):
    rounded(draw, (12, 12, 52, 52), 10, color)
    line(draw, [(18, 41), (26, 33), (34, 37), (45, 23)], color)
    line(draw, [(37, 23), (45, 23), (45, 31)], color)
    draw.arc((p(17), p(17), p(47), p(47)), 200, 338, fill=GOLD if color != MUTED else color, width=p(2.5))


def draw_procurement(draw, color):
    rounded(draw, (8, 19, 31, 44), 5, color)
    rounded(draw, (33, 20, 56, 45), 5, color)
    line(draw, [(22, 32), (42, 32)], color)
    line(draw, [(37, 27), (42, 32), (37, 37)], color)
    line(draw, [(14, 19), (18, 12), (25, 12), (28, 19)], color, 2.3)
    line(draw, [(43, 20), (48, 13), (54, 14)], color, 2.3)


def draw_analytics(draw, color):
    line(draw, [(12, 53), (55, 53)], color, 2.5)
    for x, y in ((17, 42), (29, 31), (41, 37), (51, 18)):
        rounded(draw, (x - 4, y, x + 4, 53), 3, color, 0, fill=color)
    line(draw, [(14, 36), (26, 25), (38, 31), (51, 15)], GOLD if color != MUTED else color, 2.3)


def draw_automation(draw, color):
    for box in ((9, 10, 25, 25), (39, 10, 55, 25), (24, 40, 40, 55)):
        rounded(draw, box, 4, color, 2.8)
    line(draw, [(25, 18), (39, 18)], color, 2.2)
    line(draw, [(17, 25), (25, 47)], color, 2.2)
    line(draw, [(47, 25), (39, 47)], color, 2.2)
    draw.ellipse((p(28), p(15), p(36), p(23)), fill=TEAL if color != MUTED else color)


def draw_settings(draw, color):
    for y, knob in ((17, 42), (32, 24), (47, 37)):
        line(draw, [(10, y), (54, y)], color, 2.4)
        draw.ellipse((p(knob - 6), p(y - 6), p(knob + 6), p(y + 6)), fill=INK, outline=color, width=p(3))


def draw_account(draw, color):
    draw.ellipse((p(10), p(9), p(54), p(53)), outline=color, width=p(3))
    draw.ellipse((p(25), p(19), p(39), p(33)), outline=color, width=p(2.8))
    draw.arc((p(19), p(31), p(45), p(51)), 190, 350, fill=color, width=p(3))


def draw_service(draw, color):
    rounded(draw, (9, 12, 55, 45), 9, color)
    line(draw, [(19, 45), (17, 54), (29, 45)], color)
    for x in (21, 32, 43):
        draw.ellipse((p(x - 2), p(26), p(x + 2), p(30)), fill=color)


def draw_marketing(draw, color):
    line(draw, [(13, 50), (45, 18)], color)
    draw.ellipse((p(8), p(45), p(20), p(57)), outline=color, width=p(3))
    draw.ellipse((p(41), p(10), p(55), p(24)), outline=color, width=p(3))
    line(draw, [(13, 18), (20, 18), (26, 24), (33, 32)], color, 3)
    line(draw, [(13, 18), (13, 31), (21, 39), (28, 45)], color, 3)


DRAWERS = {
    "home": draw_home,
    "store": draw_store,
    "orders": draw_orders,
    "shipping": draw_shipping,
    "products": draw_products,
    "operations": draw_operations,
    "procurement": draw_procurement,
    "analytics": draw_analytics,
    "automation": draw_automation,
    "settings": draw_settings,
    "account": draw_account,
    "service": draw_service,
    "marketing": draw_marketing,
}


def save_logo() -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            mix = (x + y) / (size * 2)
            pixels[x, y] = (
                round(20 * (1 - mix) + 10 * mix),
                round(74 * (1 - mix) + 45 * mix),
                round(75 * (1 - mix) + 48 * mix),
                255,
            )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((4, 4, 251, 251), radius=58, fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    draw.arc((37, 55, 219, 210), 198, 342, fill=ACTIVE, width=15)
    draw.arc((72, 84, 224, 218), 203, 338, fill=GOLD, width=11)
    draw.ellipse((172, 54, 198, 80), fill=ACTIVE)
    image.save(ASSETS / "logo.png")
    image.save(ASSETS / "icon.ico", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, painter in DRAWERS.items():
        for state, color in (("normal", MUTED), ("active", ACTIVE)):
            image, draw = canvas()
            painter(draw, color)
            image = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
            image.save(ICONS / f"{name}_{state}.png")
    save_logo()
    print(f"Generated brand assets in {ASSETS}")


if __name__ == "__main__":
    main()
