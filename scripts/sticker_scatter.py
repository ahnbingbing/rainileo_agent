"""
scripts/sticker_scatter.py
--------------------------
Scatter decorative PNG stickers across a 1080x1920 transparent canvas
so the main render can overlay the result on a video cut.

Design goals
------------
* **Auto-discovery**: stickers are loaded from `assets/stickers/<category>/*.png`.
  To add new stickers, drop PNGs in those folders — no code change required.
* **Avoid the centre band** so the pets' faces stay clear. The "safe zone"
  for stickers is the outer 30% on each side and top/bottom corners.
* **Reproducible per cut**: the random seed is derived from cut.idx so the
  same cut always produces the same scatter (good for iterating without
  visual jitter on each render).
* **Layered**: each sticker can be rotated and scaled within bounds; soft
  drop-shadow already baked into the source PNGs by bootstrap_stickers.py.

Public API
----------
    StickerPack(name, categories, count, size_range)
    render_scatter_png(cut, out_path) -> Path
    available_stickers() -> dict[str, list[Path]]
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STICKERS_ROOT = ROOT / "assets" / "stickers"
CANVAS_W, CANVAS_H = 1080, 1920


# ─────────────────────────────────────────────────────────────────────
# Sticker discovery
# ─────────────────────────────────────────────────────────────────────
def available_stickers() -> dict[str, list[Path]]:
    """Return {category: [png_path, ...]} for every subfolder."""
    out: dict[str, list[Path]] = {}
    if not STICKERS_ROOT.is_dir():
        return out
    for sub in sorted(STICKERS_ROOT.iterdir()):
        if not sub.is_dir():
            continue
        pngs = sorted(p for p in sub.iterdir()
                      if p.is_file() and p.suffix.lower() == ".png"
                      and not p.name.startswith("."))
        if pngs:
            out[sub.name] = pngs
    return out


# ─────────────────────────────────────────────────────────────────────
# Scatter recipe
# ─────────────────────────────────────────────────────────────────────
@dataclass
class StickerPack:
    """Per-cut decoration recipe.

    placement   : how to arrange the stickers within the canvas.
                  - "scatter"     : random placement outside avoid_box (legacy).
                  - "halo"        : small ring above subject_center, like a
                                    sparkle halo over a pet's head.
                  - "edge_border" : tight strips along top & bottom only,
                                    leaves the middle clear.
    categories  : pull stickers from these subfolders
    count       : how many stickers to drop into the cut
    size_range  : (min_px, max_px) for the longest edge of each sticker
    rotation    : (min_deg, max_deg) random rotation
    avoid_box   : (x0, y0, x1, y1) keep-out rectangle (pet faces). All in
                  canvas px. Only used by `placement="scatter"`.
                  defaults to a generous middle band.
    subject_center : (x_pct, y_pct) of subject's head — required for
                     placement="halo". Pct of canvas (0..1).
    halo_radius_pct: ring radius as fraction of CANVAS_H (default 0.10 ≈ 192px).
                     Halo sits CENTERED ON subject_center but the y is shifted
                     up by halo_radius_pct so the ring crowns the head.
    halo_arc_deg   : (start_deg, end_deg). 180..360 places stickers across
                     the upper half (12 o'clock = 270°). Defaults to a wide
                     arc above the subject.
    seed        : optional override; defaults to derive from cut.idx + name.
    """
    categories: list[str]
    count: int = 4
    size_range: tuple[int, int] = (140, 230)
    rotation: tuple[int, int] = (-25, 25)
    avoid_box: tuple[int, int, int, int] | None = None
    placement: str = "scatter"
    subject_center: tuple[float, float] | None = None
    halo_radius_pct: float = 0.10
    halo_arc_deg: tuple[int, int] = (200, 340)   # roughly a smile-shape arc above
    seed: int | None = None

    def with_avoid_box(self, x0, y0, x1, y1) -> "StickerPack":
        return StickerPack(
            categories=self.categories,
            count=self.count,
            size_range=self.size_range,
            rotation=self.rotation,
            avoid_box=(x0, y0, x1, y1),
            placement=self.placement,
            subject_center=self.subject_center,
            halo_radius_pct=self.halo_radius_pct,
            halo_arc_deg=self.halo_arc_deg,
            seed=self.seed,
        )


@dataclass
class FixedSticker:
    """A specific sticker pinned to a precise canvas location.

    Used for solo-intro labels (pink heart on Ryani, orange heart on Leo).
    """
    sticker_path: Path
    x_pct: float
    y_pct: float
    size: int = 280
    rotation_deg: float = 0.0
    label: str | None = None        # optional name pill below the sticker
    label_color: str = "#ff5c8a"    # outline color for the pill


# ─────────────────────────────────────────────────────────────────────
# Drawing
# ─────────────────────────────────────────────────────────────────────
def _safe_zone_points(rng: random.Random,
                      avoid: tuple[int, int, int, int],
                      margin: int = 120) -> tuple[int, int]:
    """Pick a random (x, y) inside canvas but outside the avoid_box,
    keeping a `margin` from canvas edges so stickers don't get clipped."""
    ax0, ay0, ax1, ay1 = avoid
    for _ in range(40):
        x = rng.randint(margin, CANVAS_W - margin)
        y = rng.randint(margin, CANVAS_H - margin)
        if not (ax0 <= x <= ax1 and ay0 <= y <= ay1):
            return x, y
    # fallback: top-left corner band
    return rng.randint(margin, 280), rng.randint(margin, 480)


def _halo_points(rng: random.Random,
                 subject_center: tuple[float, float],
                 radius_pct: float,
                 arc_deg: tuple[int, int],
                 count: int,
                 jitter_px: int = 18) -> list[tuple[int, int]]:
    """Place `count` points along an arc ABOVE the subject_center, like a halo.

    Coordinate convention: PIL has y-axis pointing DOWN. We treat angle 270°
    as the 12-o'clock position directly above the subject. arc_deg=(200, 340)
    spans roughly the upper half-circle (left shoulder → top → right shoulder).
    """
    cx = subject_center[0] * CANVAS_W
    cy = subject_center[1] * CANVAS_H
    radius = radius_pct * CANVAS_H
    a0, a1 = arc_deg
    points: list[tuple[int, int]] = []
    if count <= 0:
        return points
    # Evenly distribute across the arc, with a small angular jitter so it
    # doesn't read as mathematically perfect.
    span = (a1 - a0) / max(1, count - 1) if count > 1 else 0
    for i in range(count):
        a_deg = a0 + i * span + rng.uniform(-4, 4)
        a_rad = math.radians(a_deg)
        # In screen space y points down, so we subtract sin component (sin of
        # 270° ≈ -1 → ends up at cy - radius, i.e. above the subject).
        x = cx + radius * math.cos(a_rad)
        y = cy + radius * math.sin(a_rad)
        # tiny radial jitter
        x += rng.randint(-jitter_px, jitter_px)
        y += rng.randint(-jitter_px, jitter_px)
        # clamp to canvas with a small margin
        x = max(60, min(CANVAS_W - 60, int(x)))
        y = max(60, min(CANVAS_H - 60, int(y)))
        points.append((x, y))
    return points


def _edge_border_points(rng: random.Random,
                        count: int,
                        top_band_pct: tuple[float, float] = (0.04, 0.16),
                        bottom_band_pct: tuple[float, float] = (0.74, 0.84),
                        side_inset_pct: float = 0.06) -> list[tuple[int, int]]:
    """Place `count` points in two narrow horizontal strips: a top band
    and a bottom band (above the caption strip). Splits roughly even between
    top and bottom; alternates left/right within each strip so the result
    reads as a tasteful frame instead of a scatter."""
    points: list[tuple[int, int]] = []
    if count <= 0:
        return points
    x_min = int(CANVAS_W * side_inset_pct)
    x_max = int(CANVAS_W * (1 - side_inset_pct))
    top_y0 = int(CANVAS_H * top_band_pct[0])
    top_y1 = int(CANVAS_H * top_band_pct[1])
    bot_y0 = int(CANVAS_H * bottom_band_pct[0])
    bot_y1 = int(CANVAS_H * bottom_band_pct[1])

    # Half on top, half on bottom (round up to top)
    n_top = (count + 1) // 2
    n_bot = count - n_top

    # Spread the top row evenly across x with jitter; alternate which side
    # starts further inward for visual balance.
    def spread(n: int, y0: int, y1: int) -> list[tuple[int, int]]:
        if n <= 0:
            return []
        out: list[tuple[int, int]] = []
        if n == 1:
            x = rng.randint(x_min + 80, x_max - 80)
            y = rng.randint(y0, y1)
            return [(x, y)]
        for i in range(n):
            frac = i / (n - 1)
            x = int(x_min + frac * (x_max - x_min))
            x += rng.randint(-40, 40)
            y = rng.randint(y0, y1)
            out.append((x, y))
        return out

    points.extend(spread(n_top, top_y0, top_y1))
    points.extend(spread(n_bot, bot_y0, bot_y1))
    return points


def _rotate_resize(im: Image.Image, target_long: int, deg: float) -> Image.Image:
    w, h = im.size
    scale = target_long / max(w, h)
    new = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return new.rotate(deg, resample=Image.BICUBIC, expand=True)


def _paste_centre(canvas: Image.Image, sticker: Image.Image, cx: int, cy: int) -> None:
    sw, sh = sticker.size
    canvas.alpha_composite(sticker, (cx - sw // 2, cy - sh // 2))


def _draw_label_pill(canvas: Image.Image, cx: int, cy: int,
                     label: str, outline_hex: str) -> None:
    from PIL import ImageDraw, ImageFont
    fonts = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    font_path = next((p for p in fonts if Path(p).exists()), None)
    font = (ImageFont.truetype(font_path, 48) if font_path
            else ImageFont.load_default())
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 30, 14
    pw, ph = tw + 2 * pad_x, th + 2 * pad_y
    px, py = cx - pw // 2, cy - ph // 2
    h = outline_hex.lstrip("#")
    outline = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2,
                           fill=(255, 255, 255, 240),
                           outline=outline, width=5)
    draw.text((px + pad_x, py + pad_y - 4), label,
              font=font, fill=(40, 40, 40, 255))


def render_scatter(canvas: Image.Image,
                   pack: StickerPack,
                   cut_idx: int) -> None:
    """Mutate `canvas` in place: place stickers per pack.placement.

    Placement modes:
      - "scatter"     : random placement outside avoid_box (legacy)
      - "halo"        : ring above subject_center (requires subject_center)
      - "edge_border" : top/bottom strips only, never the middle band
    """
    library = available_stickers()
    pool: list[Path] = []
    for cat in pack.categories:
        pool.extend(library.get(cat, []))
    if not pool:
        return  # nothing to draw, skip silently

    seed = pack.seed if pack.seed is not None else 1000 + cut_idx
    rng = random.Random(seed)

    # Resolve placement → list of (cx, cy) points
    points: list[tuple[int, int]]
    if pack.placement == "halo":
        if pack.subject_center is None:
            # Sensible default for portrait close-ups: subject head ~ upper-center
            sc = (0.5, 0.42)
        else:
            sc = pack.subject_center
        points = _halo_points(rng, sc, pack.halo_radius_pct,
                              pack.halo_arc_deg, pack.count)
    elif pack.placement == "edge_border":
        points = _edge_border_points(rng, pack.count)
    else:
        # legacy random scatter
        avoid = pack.avoid_box or (
            # default: roughly the middle 60% horizontally, 30-75% vertically
            int(CANVAS_W * 0.20), int(CANVAS_H * 0.30),
            int(CANVAS_W * 0.80), int(CANVAS_H * 0.75),
        )
        points = [_safe_zone_points(rng, avoid) for _ in range(pack.count)]

    for cx, cy in points:
        src_path = rng.choice(pool)
        try:
            with Image.open(src_path) as im:
                im = im.convert("RGBA")
        except Exception:
            continue
        target_long = rng.randint(*pack.size_range)
        deg = rng.uniform(*pack.rotation)
        sticker = _rotate_resize(im, target_long, deg)
        _paste_centre(canvas, sticker, cx, cy)


def render_fixed_sticker(canvas: Image.Image, fs: FixedSticker) -> None:
    """Mutate `canvas` in place: paste a precisely positioned label sticker."""
    if not fs.sticker_path.exists():
        return
    with Image.open(fs.sticker_path) as im:
        im = im.convert("RGBA")
    sticker = _rotate_resize(im, fs.size, fs.rotation_deg)
    cx = int(fs.x_pct * CANVAS_W)
    cy = int(fs.y_pct * CANVAS_H)
    _paste_centre(canvas, sticker, cx, cy)
    if fs.label:
        # Pill sits a bit below the sticker
        _draw_label_pill(canvas, cx, cy + fs.size // 2 + 30,
                         fs.label, fs.label_color)


def find_sticker(category: str, name_contains: str) -> Path | None:
    """Look up a specific PNG by category + substring (case-insensitive)."""
    for p in available_stickers().get(category, []):
        if name_contains.lower() in p.stem.lower():
            return p
    return None


# ─────────────────────────────────────────────────────────────────────
# Self-test entry point
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    out_dir = ROOT / "data" / "tmp" / "scatter_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("library:", {k: len(v) for k, v in available_stickers().items()})
    for i, pack in enumerate([
        StickerPack(["hearts", "sparkles"], count=5),
        StickerPack(["sparkles", "paws"],   count=6, size_range=(120, 200)),
        StickerPack(["closing", "sparkles"], count=4),
        # NEW: halo above an off-center subject (mimics Leo close-up framing)
        StickerPack(["sparkles", "hearts"], count=6,
                    size_range=(55, 90),
                    placement="halo",
                    subject_center=(0.55, 0.48),
                    halo_radius_pct=0.11,
                    halo_arc_deg=(205, 335)),
        # NEW: edge_border (top + bottom strips only)
        StickerPack(["hearts", "sparkles", "cute"], count=6,
                    size_range=(75, 110),
                    placement="edge_border"),
    ], start=1):
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        render_scatter(canvas, pack, cut_idx=i)
        p = out_dir / f"scatter_{i:02d}.png"
        canvas.save(p, "PNG", optimize=True)
        print(" ", p.relative_to(ROOT))
    pink = find_sticker("hearts", "pink")
    if pink:
        c = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        render_fixed_sticker(c, FixedSticker(pink, 0.22, 0.28, size=280,
                                             label="랴니",
                                             label_color="#ff5c8a"))
        p = out_dir / "fixed_label.png"
        c.save(p, "PNG", optimize=True)
        print(" ", p.relative_to(ROOT))
