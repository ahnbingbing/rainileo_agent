"""
scripts/deco_anim.py
--------------------
Per-frame animated decoration for kawaii-pet-channel style shorts.

Why per-frame?
    The original `sticker_scatter` baked a single PNG of decoration and let
    ffmpeg overlay it for the whole cut. That gives a "slideshow of stickers"
    feel — every sticker present from frame 0, nothing moves. For a real
    Korean/Japanese pet-channel vibe we need:

      - dense decoration: head halo + face accents + body hearts + corner
        sparkles + edge ribbon. Often 20–30 stickers per cut.
      - motion: pop-in entrance, gentle float, wobble rotation, pulse scale.
      - staggered timing: stickers appear at different times so the cut
        keeps surprising the viewer.

This module renders ONE PNG per output frame. The render pipeline then
overlays the image sequence on the underlying photo/video cut.

Public API
----------
    AnimSticker      — one sticker with motion params
    DecoScene        — a list of AnimStickers + duration_sec + fps
    render_frames(scene, out_dir, *, prefix="deco_") -> int
    # recipe helpers (return list[AnimSticker]) ↓
    halo_ring(...)        — small ring above a subject head, gentle float
    face_accents(...)     — blush dots + small heart near face
    body_hearts(...)      — hearts scattered around the body, pop-in over time
    corner_sparkles()     — sparkles in all four corners
    edge_ribbon(...)      — top + bottom strip of small stickers
    burst_at(...)         — a single sticker that pops in once and pulses
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
# Sticker discovery (mirrors sticker_scatter.available_stickers)
# ─────────────────────────────────────────────────────────────────────
def available_stickers() -> dict[str, list[Path]]:
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


def pick(rng: random.Random, library: dict[str, list[Path]],
         categories: list[str]) -> Path | None:
    pool: list[Path] = []
    for c in categories:
        pool.extend(library.get(c, []))
    if not pool:
        return None
    return rng.choice(pool)


# ─────────────────────────────────────────────────────────────────────
# Anim sticker / scene
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AnimSticker:
    """A single decorative element with motion.

    Position is in canvas px (0..1080, 0..1920). Use the recipe helpers
    if you want to think in 0..1 fractions instead.

    Motion model
    ------------
    At time `t` seconds into the cut:
      - if t < appear_t          → not drawn
      - 0 <= u = (t-appear_t)/pop_in_dur <= 1 → pop_scale = ease_out_back(u)
      - u > 1                    → pop_scale = 1.0
      pop_scale is multiplied into the sticker's final size.

      Additionally (continuous, regardless of pop-in state):
        y_offset = float_amp_px * sin(2π * float_freq_hz * t + float_phase)
        x_offset = drift_amp_px * sin(2π * float_freq_hz * t * 0.7 + float_phase)
        rotation = base_rot + wobble_amp_deg * sin(2π * wobble_freq_hz * t)
        pulse    = 1 + pulse_amp * sin(2π * pulse_freq_hz * t + pulse_phase)
    """
    sticker_path: Path
    x_px: int
    y_px: int
    size: int = 90
    base_rot: float = 0.0
    # entrance
    appear_t: float = 0.0
    pop_in_dur: float = 0.30
    pop_in_overshoot: float = 0.25   # how bouncy the pop-in feels (0..0.6)
    # float (gentle bobbing)
    float_amp_px: float = 0.0
    drift_amp_px: float = 0.0
    float_freq_hz: float = 0.8
    float_phase: float = 0.0
    # wobble (rotation oscillation, small)
    wobble_amp_deg: float = 0.0
    wobble_freq_hz: float = 1.2
    # pulse (size oscillation, 0..0.3)
    pulse_amp: float = 0.0
    pulse_freq_hz: float = 1.6
    pulse_phase: float = 0.0
    # opacity (constant; future: fade in/out)
    alpha: float = 1.0


@dataclass
class DecoScene:
    duration_sec: float
    fps: int
    stickers: list[AnimSticker] = field(default_factory=list)
    # bottom caption text rendered into every frame (optional)
    caption_kr: str = ""
    caption_en: str = ""
    # Soft kawaii vignette layer drawn UNDER the stickers, OVER the photo.
    # Tints the corners with a pastel glow and adds a few drifting sparkles
    # so the whole frame reads as "magical decorated world", not bare photo.
    glow_strength: float = 0.0       # 0.0 (off) .. 0.6 (heavy pink wash)
    glow_color_rgb: tuple[int, int, int] = (255, 200, 220)   # soft pink
    glow_corners: tuple[str, ...] = ("tl", "tr", "bl", "br")  # which corners
    bg_sparkle_count: int = 0       # tiny far-background twinkles
    bg_sparkle_seed: int = 0


# ─────────────────────────────────────────────────────────────────────
# Frame rendering
# ─────────────────────────────────────────────────────────────────────
def _ease_out_back(u: float, overshoot: float = 0.25) -> float:
    """Spring ease-out for pop-in: at u=0 returns 0, at u=1 returns 1,
    overshoots slightly then settles. Classic 'back' easing curve."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    # c1 controls overshoot magnitude — derived so that final value = 1
    c1 = 1.0 + overshoot * 3.0     # default ~1.75 (Robert Penner's c1)
    c3 = c1 + 1.0
    u = u - 1.0
    return 1.0 + c3 * (u ** 3) + c1 * (u ** 2)


def _rotate_resize(im: Image.Image, target_long: int, deg: float) -> Image.Image:
    w, h = im.size
    target_long = max(8, int(target_long))
    scale = target_long / max(w, h)
    new = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS)
    if abs(deg) > 0.01:
        new = new.rotate(deg, resample=Image.BICUBIC, expand=True)
    return new


def _draw_caption(canvas: Image.Image, kr: str, en: str) -> None:
    from PIL import ImageDraw, ImageFont
    fonts_kr = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    fonts_en = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    kr_path = next((f for f in fonts_kr if Path(f).exists()), None)
    en_path = next((f for f in fonts_en if Path(f).exists()), None) or kr_path
    kr_font = (ImageFont.truetype(kr_path, 64) if kr_path
               else ImageFont.load_default())
    en_font = (ImageFont.truetype(en_path, 38) if en_path
               else ImageFont.load_default())
    draw = ImageDraw.Draw(canvas)
    block_h = 280
    block_y = CANVAS_H - block_h
    for y in range(block_y, CANVAS_H):
        ratio = (y - block_y) / block_h
        a = int(180 * ratio)
        draw.line([(0, y), (CANVAS_W, y)], fill=(0, 0, 0, a))

    def centered(text, font, y, fill=(255, 255, 255, 255)):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (CANVAS_W - tw) // 2
        draw.text((x + 2, y + 3), text, font=font, fill=(0, 0, 0, 190))
        draw.text((x, y), text, font=font, fill=fill)

    if kr:
        centered(kr, kr_font, block_y + 60)
    if en:
        centered(en, en_font, block_y + 160, fill=(255, 245, 245, 240))


def _draw_sticker_at(canvas: Image.Image, s: AnimSticker, t: float) -> None:
    """Composite one AnimSticker onto canvas at time t (seconds)."""
    if t < s.appear_t:
        return
    if not s.sticker_path.exists():
        return

    # pop-in scale
    u = max(0.0, (t - s.appear_t) / max(0.001, s.pop_in_dur))
    pop = _ease_out_back(u, s.pop_in_overshoot)
    if pop <= 0:
        return

    # pulse
    pulse = 1.0 + s.pulse_amp * math.sin(
        2 * math.pi * s.pulse_freq_hz * t + s.pulse_phase
    )

    # rotation
    rot = s.base_rot + s.wobble_amp_deg * math.sin(
        2 * math.pi * s.wobble_freq_hz * t
    )

    # position (float + drift)
    yoff = s.float_amp_px * math.sin(
        2 * math.pi * s.float_freq_hz * t + s.float_phase
    )
    xoff = s.drift_amp_px * math.sin(
        2 * math.pi * s.float_freq_hz * t * 0.7 + s.float_phase
    )

    size = max(8, int(s.size * pop * pulse))
    try:
        with Image.open(s.sticker_path) as im:
            im = im.convert("RGBA")
    except Exception:
        return
    sticker = _rotate_resize(im, size, rot)

    # alpha
    if s.alpha < 0.999:
        a = sticker.split()[-1]
        a = a.point(lambda px: int(px * max(0.0, min(1.0, s.alpha))))
        sticker.putalpha(a)

    cx = int(s.x_px + xoff)
    cy = int(s.y_px + yoff)
    sw, sh = sticker.size
    canvas.alpha_composite(sticker, (cx - sw // 2, cy - sh // 2))


def _draw_glow_corners(canvas: Image.Image,
                       color_rgb: tuple[int, int, int],
                       strength: float,
                       corners: tuple[str, ...]) -> None:
    """Paint pastel radial glows in each requested corner.
    Uses a soft-light feel: corners get tinted, center stays mostly clear."""
    if strength <= 0:
        return
    from PIL import ImageDraw
    overlay = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    # Glow radius — large soft falloff
    radius = int(min(CANVAS_W, CANVAS_H) * 0.55)
    centers = {
        "tl": (0, 0),
        "tr": (CANVAS_W, 0),
        "bl": (0, CANVAS_H),
        "br": (CANVAS_W, CANVAS_H),
    }
    max_alpha = int(180 * max(0.0, min(1.0, strength)))
    for key in corners:
        if key not in centers:
            continue
        cx, cy = centers[key]
        # draw concentric circles with decreasing alpha → soft falloff
        steps = 14
        for i in range(steps):
            frac = i / (steps - 1)
            r = int(radius * (1.0 - frac * 0.85))
            a = int(max_alpha * (frac ** 1.6))
            if r <= 0 or a <= 0:
                continue
            draw = ImageDraw.Draw(overlay)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         fill=(*color_rgb, a))
    canvas.alpha_composite(overlay)


def _draw_bg_sparkles(canvas: Image.Image, t: float, count: int, seed: int,
                      duration_sec: float) -> None:
    """Tiny background twinkles — small white dots that fade in/out on a
    slow sine. Drawn UNDER foreground stickers."""
    if count <= 0:
        return
    from PIL import ImageDraw
    rng = random.Random(seed)
    draw = ImageDraw.Draw(canvas)
    for i in range(count):
        x = rng.randint(40, CANVAS_W - 40)
        # Bias sparkles to upper 75% so they don't fight the caption strip
        y = rng.randint(40, int(CANVAS_H * 0.72))
        phase = rng.uniform(0, 2 * math.pi)
        freq = rng.uniform(0.4, 0.9)
        # Fade with a sine: alpha 0..220
        a = 0.5 + 0.5 * math.sin(2 * math.pi * freq * t + phase)
        alpha = int(220 * a)
        if alpha < 8:
            continue
        r = rng.randint(3, 6)
        draw.ellipse([x - r, y - r, x + r, y + r],
                     fill=(255, 255, 255, alpha))
        # tiny diffraction cross
        draw.line([x - r * 2, y, x + r * 2, y],
                  fill=(255, 255, 255, alpha // 2), width=1)
        draw.line([x, y - r * 2, x, y + r * 2],
                  fill=(255, 255, 255, alpha // 2), width=1)


def render_frames(scene: DecoScene, out_dir: Path,
                  prefix: str = "deco_") -> int:
    """Render PNG frames `<prefix>NNNN.png` (1-indexed) in out_dir.
    Returns frame count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n_frames = max(1, int(round(scene.duration_sec * scene.fps)))
    for i in range(n_frames):
        t = i / scene.fps
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        # 1) soft pastel corner glow (under everything)
        if scene.glow_strength > 0:
            _draw_glow_corners(canvas, scene.glow_color_rgb,
                               scene.glow_strength, scene.glow_corners)
        # 2) background twinkles (still under stickers, but on top of glow)
        if scene.bg_sparkle_count > 0:
            _draw_bg_sparkles(canvas, t, scene.bg_sparkle_count,
                              scene.bg_sparkle_seed, scene.duration_sec)
        # 3) animated stickers
        for s in scene.stickers:
            _draw_sticker_at(canvas, s, t)
        # 4) caption strip (always on top)
        if scene.caption_kr or scene.caption_en:
            _draw_caption(canvas, scene.caption_kr, scene.caption_en)
        canvas.save(out_dir / f"{prefix}{i+1:04d}.png", "PNG", optimize=False)
    return n_frames


# ─────────────────────────────────────────────────────────────────────
# Recipes — return list[AnimSticker]
# ─────────────────────────────────────────────────────────────────────
def halo_ring(rng: random.Random,
              subject_center: tuple[float, float],
              radius_pct: float = 0.10,
              count: int = 7,
              arc_deg: tuple[int, int] = (200, 340),
              size_range: tuple[int, int] = (60, 95),
              categories: list[str] | None = None,
              float_amp_px: float = 10,
              wobble_amp_deg: float = 6,
              stagger_t: float = 0.06,
              pop_in_dur: float = 0.30,
              base_t: float = 0.0,
              library: dict[str, list[Path]] | None = None) -> list[AnimSticker]:
    """Halo of small stickers arcing above subject_center (x_pct, y_pct).
    Each sticker has gentle float + light wobble. Pop-in is staggered."""
    library = library or available_stickers()
    categories = categories or ["sparkles", "hearts"]
    cx_px = subject_center[0] * CANVAS_W
    cy_px = subject_center[1] * CANVAS_H
    radius = radius_pct * CANVAS_H
    a0, a1 = arc_deg
    out: list[AnimSticker] = []
    if count <= 0:
        return out
    span = (a1 - a0) / max(1, count - 1) if count > 1 else 0
    for i in range(count):
        a_deg = a0 + i * span + rng.uniform(-3, 3)
        a_rad = math.radians(a_deg)
        x = cx_px + radius * math.cos(a_rad) + rng.randint(-8, 8)
        y = cy_px + radius * math.sin(a_rad) + rng.randint(-8, 8)
        path = pick(rng, library, categories)
        if path is None:
            continue
        out.append(AnimSticker(
            sticker_path=path,
            x_px=int(x), y_px=int(y),
            size=rng.randint(*size_range),
            base_rot=rng.uniform(-12, 12),
            appear_t=base_t + i * stagger_t,
            pop_in_dur=pop_in_dur,
            pop_in_overshoot=0.30,
            float_amp_px=float_amp_px,
            float_freq_hz=rng.uniform(0.6, 0.9),
            float_phase=rng.uniform(0, 2 * math.pi),
            wobble_amp_deg=wobble_amp_deg,
            wobble_freq_hz=rng.uniform(0.9, 1.4),
            pulse_amp=0.04,
            pulse_freq_hz=rng.uniform(1.3, 1.9),
        ))
    return out


def face_accents(rng: random.Random,
                 subject_center: tuple[float, float],
                 size_range: tuple[int, int] = (70, 130),
                 base_t: float = 0.0,
                 library: dict[str, list[Path]] | None = None
                 ) -> list[AnimSticker]:
    """Near-face accents: a small heart on one cheek + a sparkle on the
    other + a star tucked above the eyes. Wobble + slight pulse so they
    feel attached to the face."""
    library = library or available_stickers()
    cx_px = subject_center[0] * CANVAS_W
    cy_px = subject_center[1] * CANVAS_H
    out: list[AnimSticker] = []
    # left cheek (heart)
    h = pick(rng, library, ["hearts"])
    if h is not None:
        out.append(AnimSticker(
            sticker_path=h,
            x_px=int(cx_px - 0.10 * CANVAS_W),
            y_px=int(cy_px + 0.04 * CANVAS_H),
            size=rng.randint(*size_range),
            base_rot=rng.uniform(-18, -8),
            appear_t=base_t + 0.15,
            pop_in_dur=0.25,
            wobble_amp_deg=4,
            wobble_freq_hz=1.4,
            pulse_amp=0.06,
            pulse_freq_hz=1.8,
        ))
    # right cheek (sparkle or star)
    s = pick(rng, library, ["sparkles"])
    if s is not None:
        out.append(AnimSticker(
            sticker_path=s,
            x_px=int(cx_px + 0.09 * CANVAS_W),
            y_px=int(cy_px + 0.03 * CANVAS_H),
            size=rng.randint(*size_range),
            base_rot=rng.uniform(8, 18),
            appear_t=base_t + 0.25,
            pop_in_dur=0.25,
            wobble_amp_deg=5,
            wobble_freq_hz=1.6,
            pulse_amp=0.07,
            pulse_freq_hz=2.0,
        ))
    return out


def body_hearts(rng: random.Random,
                subject_center: tuple[float, float],
                count: int = 5,
                spread_pct: tuple[float, float] = (0.18, 0.26),
                size_range: tuple[int, int] = (55, 95),
                base_t: float = 0.4,
                stagger_t: float = 0.10,
                library: dict[str, list[Path]] | None = None
                ) -> list[AnimSticker]:
    """Hearts floating around the body — random positions in an annulus
    around subject_center, drifting/floating, with staggered pop-in."""
    library = library or available_stickers()
    cx_px = subject_center[0] * CANVAS_W
    cy_px = subject_center[1] * CANVAS_H
    r_min = spread_pct[0] * CANVAS_H
    r_max = spread_pct[1] * CANVAS_H
    out: list[AnimSticker] = []
    for i in range(count):
        for _ in range(20):  # retry to land within canvas
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(r_min, r_max)
            x = cx_px + r * math.cos(a)
            y = cy_px + r * math.sin(a) - 0.04 * CANVAS_H  # bias upward
            if 80 < x < CANVAS_W - 80 and 120 < y < CANVAS_H - 360:
                break
        else:
            continue
        path = pick(rng, library, ["hearts"])
        if path is None:
            continue
        out.append(AnimSticker(
            sticker_path=path,
            x_px=int(x), y_px=int(y),
            size=rng.randint(*size_range),
            base_rot=rng.uniform(-20, 20),
            appear_t=base_t + i * stagger_t,
            pop_in_dur=0.30,
            float_amp_px=rng.uniform(8, 16),
            drift_amp_px=rng.uniform(4, 9),
            float_freq_hz=rng.uniform(0.5, 0.9),
            float_phase=rng.uniform(0, 2 * math.pi),
            wobble_amp_deg=rng.uniform(3, 6),
            wobble_freq_hz=rng.uniform(0.7, 1.2),
            pulse_amp=0.05,
            pulse_freq_hz=rng.uniform(1.0, 1.6),
        ))
    return out


def corner_sparkles(rng: random.Random,
                    size_range: tuple[int, int] = (60, 110),
                    base_t: float = 0.0,
                    library: dict[str, list[Path]] | None = None,
                    categories: list[str] | None = None
                    ) -> list[AnimSticker]:
    """Sparkles tucked into the four corners (excluding bottom caption strip)."""
    library = library or available_stickers()
    categories = categories or ["sparkles", "cute"]
    out: list[AnimSticker] = []
    corners = [
        (0.08, 0.06),  # top-left
        (0.92, 0.06),  # top-right
        (0.08, 0.72),  # mid-low-left (above caption)
        (0.92, 0.72),  # mid-low-right
    ]
    for i, (px, py) in enumerate(corners):
        path = pick(rng, library, categories)
        if path is None:
            continue
        out.append(AnimSticker(
            sticker_path=path,
            x_px=int(px * CANVAS_W),
            y_px=int(py * CANVAS_H),
            size=rng.randint(*size_range),
            base_rot=rng.uniform(-15, 15),
            appear_t=base_t + i * 0.08,
            pop_in_dur=0.35,
            float_amp_px=6,
            float_freq_hz=rng.uniform(0.5, 0.8),
            float_phase=rng.uniform(0, 2 * math.pi),
            wobble_amp_deg=rng.uniform(2, 5),
            wobble_freq_hz=rng.uniform(0.8, 1.3),
            pulse_amp=0.05,
            pulse_freq_hz=rng.uniform(1.1, 1.7),
        ))
    return out


def edge_ribbon(rng: random.Random,
                count_top: int = 3,
                count_bottom: int = 2,
                size_range: tuple[int, int] = (55, 95),
                top_y_pct: tuple[float, float] = (0.05, 0.13),
                bottom_y_pct: tuple[float, float] = (0.74, 0.82),
                side_inset_pct: float = 0.18,
                categories: list[str] | None = None,
                base_t: float = 0.0,
                library: dict[str, list[Path]] | None = None
                ) -> list[AnimSticker]:
    """Small stickers along top and bottom edges as a tasteful frame."""
    library = library or available_stickers()
    categories = categories or ["hearts", "sparkles", "cute"]
    out: list[AnimSticker] = []
    x_min = side_inset_pct * CANVAS_W
    x_max = (1 - side_inset_pct) * CANVAS_W

    def make_row(n: int, y0p: float, y1p: float, t0: float) -> list[AnimSticker]:
        row: list[AnimSticker] = []
        if n <= 0:
            return row
        for i in range(n):
            frac = i / (n - 1) if n > 1 else 0.5
            x = x_min + frac * (x_max - x_min) + rng.randint(-25, 25)
            y = rng.uniform(y0p, y1p) * CANVAS_H
            path = pick(rng, library, categories)
            if path is None:
                continue
            row.append(AnimSticker(
                sticker_path=path,
                x_px=int(x), y_px=int(y),
                size=rng.randint(*size_range),
                base_rot=rng.uniform(-25, 25),
                appear_t=t0 + i * 0.07,
                pop_in_dur=0.30,
                float_amp_px=rng.uniform(4, 9),
                float_freq_hz=rng.uniform(0.5, 0.9),
                float_phase=rng.uniform(0, 2 * math.pi),
                wobble_amp_deg=rng.uniform(2, 5),
                wobble_freq_hz=rng.uniform(0.8, 1.3),
                pulse_amp=0.04,
            ))
        return row

    out.extend(make_row(count_top, *top_y_pct, base_t + 0.05))
    out.extend(make_row(count_bottom, *bottom_y_pct, base_t + 0.25))
    return out


def burst_at(rng: random.Random,
             x_pct: float, y_pct: float,
             category: str = "hearts",
             size: int = 180,
             appear_t: float = 0.2,
             pulse_amp: float = 0.10,
             library: dict[str, list[Path]] | None = None
             ) -> list[AnimSticker]:
    """A single hero sticker that pops in and pulses (for closeness beats)."""
    library = library or available_stickers()
    path = pick(rng, library, [category])
    if path is None:
        return []
    return [AnimSticker(
        sticker_path=path,
        x_px=int(x_pct * CANVAS_W),
        y_px=int(y_pct * CANVAS_H),
        size=size,
        base_rot=rng.uniform(-8, 8),
        appear_t=appear_t,
        pop_in_dur=0.35,
        pop_in_overshoot=0.45,
        wobble_amp_deg=3,
        wobble_freq_hz=1.2,
        pulse_amp=pulse_amp,
        pulse_freq_hz=1.8,
    )]


# ─────────────────────────────────────────────────────────────────────
# Self-test: render 6 sample frames of a dense halo + body + corners scene
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    out_dir = ROOT / "data" / "tmp" / "deco_anim_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    lib = available_stickers()
    print("library:", {k: len(v) for k, v in lib.items()})

    stickers = []
    stickers += corner_sparkles(rng, library=lib)
    stickers += halo_ring(rng, (0.50, 0.42),
                          radius_pct=0.11, count=8,
                          arc_deg=(195, 345),
                          size_range=(65, 100),
                          library=lib, base_t=0.0)
    stickers += face_accents(rng, (0.50, 0.50), library=lib, base_t=0.10)
    stickers += body_hearts(rng, (0.50, 0.62), count=6,
                            spread_pct=(0.16, 0.24), library=lib, base_t=0.30)
    stickers += edge_ribbon(rng, count_top=3, count_bottom=2,
                            library=lib, base_t=0.40)
    print("total stickers:", len(stickers))

    scene = DecoScene(duration_sec=2.5, fps=30, stickers=stickers,
                      caption_kr="안녕! 나는 랴니예요",
                      caption_en="Hi! I'm Ryani")
    n = render_frames(scene, out_dir, prefix="deco_")
    print("rendered", n, "frames →", out_dir)
