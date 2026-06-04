"""
scripts/bootstrap_stickers.py
-----------------------------
Generate a starter set of decorative sticker PNGs (240x240, transparent
background) into assets/stickers/{category}/.  These are placeholders
so the scatter system can be tested end-to-end; real subscription
stickers will replace them later — drop new PNGs into the same folders
and re-run render.

Categories
    hearts/    pink, red, gold, magenta, peach
    sparkles/  white star, gold star, 4-point sparkle, dot cluster
    paws/      pink paw, brown paw, white paw
    cute/      cloud, flower, bow
    closing/   crescent moon, alarm clock
    music/     eighth note, double note

Usage
    python3 scripts/bootstrap_stickers.py
"""
from __future__ import annotations
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
STICKERS = ROOT / "assets" / "stickers"
SIZE = 240
MARGIN = 20  # leave inside SIZE for shadow + outline


def new_canvas() -> Image.Image:
    return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))


def add_shadow(layer: Image.Image, offset: int = 6, blur: int = 8) -> Image.Image:
    """Composite a soft drop-shadow under whatever is on `layer`."""
    alpha = layer.split()[-1]
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow_alpha = alpha.point(lambda a: min(140, a))
    shadow.putalpha(shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(shadow, (offset, offset), shadow)
    out.alpha_composite(layer)
    return out


def add_outline(layer: Image.Image, color=(255, 255, 255, 255), width: int = 6) -> Image.Image:
    """White outline by dilating the alpha channel and stamping color underneath."""
    alpha = layer.split()[-1]
    dilated = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline = Image.new("RGBA", layer.size, color)
    outline.putalpha(dilated)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(outline)
    out.alpha_composite(layer)
    return out


# ── shape generators ────────────────────────────────────────────────
def heart_shape(color: tuple) -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    # Use a parametric heart curve and fill with polygon for smoothness.
    pts = []
    for t in [i / 240 for i in range(241)]:
        ang = t * 2 * math.pi
        x = 16 * math.sin(ang) ** 3
        y = -(13 * math.cos(ang)
              - 5 * math.cos(2 * ang)
              - 2 * math.cos(3 * ang)
              - math.cos(4 * ang))
        pts.append((cx + x * 6, cy + y * 6 - 14))
    draw.polygon(pts, fill=color)
    return img


def star_shape(color: tuple, points: int = 5) -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    r_outer = SIZE // 2 - MARGIN
    r_inner = r_outer * 0.45
    pts = []
    for i in range(points * 2):
        r = r_outer if i % 2 == 0 else r_inner
        ang = -math.pi / 2 + i * math.pi / points
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts, fill=color)
    return img


def sparkle_shape(color: tuple) -> Image.Image:
    """4-pointed sparkle (long verticals + short horizontals)."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    r_long = SIZE // 2 - MARGIN
    r_short = r_long * 0.35
    pts = [
        (cx, cy - r_long),
        (cx + r_short * 0.4, cy - r_short),
        (cx + r_short, cy),
        (cx + r_short * 0.4, cy + r_short),
        (cx, cy + r_long),
        (cx - r_short * 0.4, cy + r_short),
        (cx - r_short, cy),
        (cx - r_short * 0.4, cy - r_short),
    ]
    draw.polygon(pts, fill=color)
    return img


def paw_shape(color: tuple) -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2 + 12
    # Big pad
    pad_w, pad_h = 130, 110
    draw.ellipse([cx - pad_w // 2, cy, cx + pad_w // 2, cy + pad_h], fill=color)
    # Toes
    toe_r = 30
    offsets = [(-50, -55), (-15, -75), (25, -75), (60, -55)]
    for ox, oy in offsets:
        draw.ellipse([cx + ox - toe_r, cy + oy - toe_r,
                      cx + ox + toe_r, cy + oy + toe_r], fill=color)
    return img


def crescent_moon() -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    r = SIZE // 2 - MARGIN
    color = (255, 220, 120, 255)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    # Cut a crescent by overpainting an offset transparent ellipse
    cutter = new_canvas()
    cdraw = ImageDraw.Draw(cutter)
    cdraw.ellipse([cx - r + 35, cy - r - 8, cx + r + 35, cy + r - 8],
                  fill=(0, 0, 0, 255))
    img_arr = list(img.getdata())
    cut_arr = list(cutter.getdata())
    new = []
    for px, cx_px in zip(img_arr, cut_arr):
        if cx_px[3] > 0:
            new.append((0, 0, 0, 0))
        else:
            new.append(px)
    img.putdata(new)
    return img


def alarm_clock() -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2 + 8
    r = 75
    # ear bells
    for ox in (-60, 60):
        draw.ellipse([cx + ox - 22, cy - r - 18, cx + ox + 22, cy - r + 18],
                     fill=(220, 100, 100, 255))
    # body
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 240, 220, 255))
    draw.ellipse([cx - r + 8, cy - r + 8, cx + r - 8, cy + r - 8],
                 fill=(255, 255, 255, 255))
    # 9 o'clock hands (since show is at 9PM)
    draw.line([cx, cy, cx, cy - 50], fill=(60, 60, 60, 255), width=8)
    draw.line([cx, cy, cx - 35, cy], fill=(60, 60, 60, 255), width=6)
    draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(60, 60, 60, 255))
    return img


def cloud_shape() -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2 + 10
    color = (255, 255, 255, 255)
    draw.ellipse([cx - 90, cy - 30, cx - 30, cy + 30], fill=color)
    draw.ellipse([cx - 50, cy - 60, cx + 30, cy + 20], fill=color)
    draw.ellipse([cx + 0, cy - 40, cx + 80, cy + 30], fill=color)
    draw.ellipse([cx - 80, cy - 5, cx + 80, cy + 45], fill=color)
    return img


def flower_shape(petal_color, center_color) -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    petal_r = 38
    for i in range(5):
        ang = -math.pi / 2 + i * 2 * math.pi / 5
        px = cx + 50 * math.cos(ang)
        py = cy + 50 * math.sin(ang)
        draw.ellipse([px - petal_r, py - petal_r, px + petal_r, py + petal_r],
                     fill=petal_color)
    draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=center_color)
    return img


def bow_shape(color) -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    # Left loop
    draw.polygon([(cx, cy), (cx - 80, cy - 50), (cx - 80, cy + 50)], fill=color)
    # Right loop
    draw.polygon([(cx, cy), (cx + 80, cy - 50), (cx + 80, cy + 50)], fill=color)
    # Knot
    draw.ellipse([cx - 18, cy - 24, cx + 18, cy + 24], fill=color)
    return img


def music_note() -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    cx = SIZE // 2 - 20
    color = (90, 110, 220, 255)
    # head
    draw.ellipse([cx - 36, SIZE - 100, cx + 22, SIZE - 50], fill=color)
    # stem
    draw.rectangle([cx + 16, 50, cx + 26, SIZE - 70], fill=color)
    # flag
    draw.polygon(
        [(cx + 26, 50), (cx + 26, 110), (cx + 80, 130), (cx + 80, 70)],
        fill=color,
    )
    return img


def double_note() -> Image.Image:
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    color = (200, 90, 180, 255)
    # Two heads
    draw.ellipse([40, 150, 95, 200], fill=color)
    draw.ellipse([130, 130, 185, 180], fill=color)
    # Stems
    draw.rectangle([88, 60, 98, 175], fill=color)
    draw.rectangle([178, 50, 188, 155], fill=color)
    # Beam
    draw.rectangle([88, 50, 188, 70], fill=color)
    return img


# ── manifest ─────────────────────────────────────────────────────────
RECIPES: list[tuple[str, str, callable]] = [
    # hearts (color variations)
    ("hearts", "heart_pink",      lambda: heart_shape((255, 110, 160, 255))),
    ("hearts", "heart_red",       lambda: heart_shape((230, 60,  80, 255))),
    ("hearts", "heart_gold",      lambda: heart_shape((255, 200, 80, 255))),
    ("hearts", "heart_magenta",   lambda: heart_shape((220, 100, 200, 255))),
    ("hearts", "heart_peach",     lambda: heart_shape((255, 170, 150, 255))),
    # sparkles
    ("sparkles", "sparkle_white", lambda: sparkle_shape((255, 255, 255, 255))),
    ("sparkles", "sparkle_gold",  lambda: sparkle_shape((255, 215, 100, 255))),
    ("sparkles", "star_gold",     lambda: star_shape((255, 215, 100, 255), 5)),
    ("sparkles", "star_white",    lambda: star_shape((255, 255, 255, 255), 5)),
    ("sparkles", "star_pink",     lambda: star_shape((255, 170, 200, 255), 5)),
    # paws
    ("paws", "paw_pink",  lambda: paw_shape((255, 140, 170, 255))),
    ("paws", "paw_brown", lambda: paw_shape((130, 90,  70,  255))),
    ("paws", "paw_white", lambda: paw_shape((250, 250, 250, 255))),
    # cute
    ("cute", "cloud_white", cloud_shape),
    ("cute", "flower_pink", lambda: flower_shape((255, 170, 200, 255), (255, 230, 120, 255))),
    ("cute", "flower_blue", lambda: flower_shape((180, 200, 255, 255), (255, 230, 120, 255))),
    ("cute", "bow_pink",    lambda: bow_shape((255, 140, 170, 255))),
    # closing icons
    ("closing", "moon_crescent", crescent_moon),
    ("closing", "alarm_9pm",     alarm_clock),
    # music
    ("music", "note_blue",   music_note),
    ("music", "note_pair",   double_note),
]


def main() -> int:
    count = 0
    for category, name, fn in RECIPES:
        out_dir = STICKERS / category
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{name}.png"
        img = fn()
        img = add_outline(img, (255, 255, 255, 255), width=5)
        img = add_shadow(img, offset=6, blur=10)
        img.save(out_path, "PNG", optimize=True)
        count += 1
        print(f"  {out_path.relative_to(ROOT)}")
    print(f"done. {count} stickers in {STICKERS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
