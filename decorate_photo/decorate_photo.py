#!/usr/bin/env python3
"""
decorate_photo.py
=================
Rule-based webtoon overlay decorator for real pet photos.

The photo itself is NEVER repainted. We only stack programmatic PIL vector
shapes (halo / hearts / sparkles / paw prints / action lines / speech
bubbles / reaction text / starburst caption) on top of the original image
in a clean Korean-webtoon reaction-mark style.

Pipeline:
  original photo  ─►  build overlay layer (PIL ImageDraw)  ─►  alpha
                       composite  ─►  decorated PNG
                       (+ 9:16 vertical export for Shorts)

Usage:
  python3 decorate_photo.py --image input.jpg
  python3 decorate_photo.py --image input.jpg --mood affectionate
  python3 decorate_photo.py --image input.jpg --mode extra_cute --seed 42

Config:
  style_config.json   — palette, sticker density, sizes, mode presets
  text_pools.json     — captions / reaction text / micro text per mood

See README.md for tuning knobs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "style_config.json"
POOLS_PATH = SCRIPT_DIR / "text_pools.json"
OUT_DIR = SCRIPT_DIR / "output"
FONT_DIR = SCRIPT_DIR / "fonts"

VERTICAL_TARGET = (1080, 1920)

MOOD_CHOICES = [
    "affectionate", "mischievous", "cute", "surprised", "calm", "playful",
]
MODE_CHOICES = ["clean", "playful", "extra_cute"]
CAPTION_POSITIONS = ["lower-left", "lower-right", "upper-left", "upper-right"]


# ──────────────────────────────────────────────────────────────────
# Font discovery — auto-detect a Korean-capable TTF.
# Search order:
#   1. decorate_photo/fonts/ (bundle your own here)
#   2. env DECORATE_PHOTO_FONT
#   3. macOS system fonts
#   4. Linux nanum / noto cjk
#   5. Pillow default (fallback — no Korean glyphs)
# ──────────────────────────────────────────────────────────────────
MAC_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
    "/Library/Fonts/AppleSDGothicNeoBold.ttf",
    "/Library/Fonts/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    "/Library/Fonts/NanumGothicBold.ttf",
]
LINUX_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def find_korean_font(bundled_dir: Path) -> Optional[Path]:
    if bundled_dir.is_dir():
        for ext in ("*.ttf", "*.otf", "*.ttc"):
            for f in sorted(bundled_dir.glob(ext)):
                return f
    env = os.environ.get("DECORATE_PHOTO_FONT")
    if env and Path(env).exists():
        return Path(env)
    for p in MAC_FONT_CANDIDATES + LINUX_FONT_CANDIDATES:
        if Path(p).exists():
            return Path(p)
    return None


def load_font(font_path: Optional[Path], size: int) -> ImageFont.ImageFont:
    if font_path and font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except Exception:
            pass
    return ImageFont.load_default()


# ──────────────────────────────────────────────────────────────────
# Geometry helpers
# ──────────────────────────────────────────────────────────────────

def rotate_points(points, center, deg):
    cx, cy = center
    r = math.radians(deg)
    cos_r, sin_r = math.cos(r), math.sin(r)
    out = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append((cx + dx * cos_r - dy * sin_r,
                    cy + dx * sin_r + dy * cos_r))
    return out


def heart_path(cx, cy, size):
    """Heart shape — parametric curve sampled to a polygon."""
    pts = []
    for t_deg in range(0, 361, 6):
        t = math.radians(t_deg)
        x = 16 * math.sin(t) ** 3
        y = (13 * math.cos(t) - 5 * math.cos(2 * t)
             - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * size / 32.0, cy - y * size / 32.0))
    return pts


def star_path(cx, cy, size, points=4, inner_ratio=0.18):
    """N-point sparkle star (default 4-point ✦)."""
    pts = []
    outer = size / 2.0
    inner = outer * inner_ratio
    for i in range(points * 2):
        angle = -math.pi / 2 + i * math.pi / points
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def paw_print_shapes(cx, cy, size):
    """Return (pad_ellipse_rect, [toe_circle_rects])."""
    s = size
    pad = (cx - s * 0.42, cy + s * 0.05, cx + s * 0.42, cy + s * 0.55)
    toe_r = s * 0.16
    toes_xy = [
        (cx - s * 0.42, cy - s * 0.18),
        (cx - s * 0.15, cy - s * 0.36),
        (cx + s * 0.15, cy - s * 0.36),
        (cx + s * 0.42, cy - s * 0.18),
    ]
    toes = [(x - toe_r, y - toe_r, x + toe_r, y + toe_r)
            for (x, y) in toes_xy]
    return pad, toes


def polygon_stroked(canvas, pts, fill, outline, outline_w):
    """Polygon fill + crisp stroke (PIL polygon's outline width support varies)."""
    canvas.polygon(pts, fill=fill, outline=outline if outline_w == 1 else None)
    if outline and outline_w > 0:
        loop = list(pts) + [pts[0]]
        canvas.line(loop, fill=outline, width=outline_w, joint="curve")


# ──────────────────────────────────────────────────────────────────
# Overlay drawing primitives
# ──────────────────────────────────────────────────────────────────

def draw_halo(canvas, center, w, h, color, stroke_w):
    cx, cy = center
    bbox = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    canvas.ellipse(bbox, outline=color, width=stroke_w)
    inset = stroke_w + max(2, int(h * 0.08))
    inner = (cx - w / 2 + inset, cy - h / 2 + inset,
             cx + w / 2 - inset, cy + h / 2 - inset)
    if inner[2] - inner[0] > 6 and inner[3] - inner[1] > 4:
        canvas.ellipse(inner, outline=color, width=max(1, stroke_w - 2))


def draw_heart(canvas, center, size, fill, outline, outline_w, rotation=0):
    pts = heart_path(*center, size=size)
    if rotation:
        pts = rotate_points(pts, center, rotation)
    polygon_stroked(canvas, pts, fill=fill,
                    outline=outline, outline_w=outline_w)


def draw_sparkle(canvas, center, size, fill, outline, outline_w):
    pts = star_path(*center, size=size, points=4, inner_ratio=0.18)
    polygon_stroked(canvas, pts, fill=fill,
                    outline=outline, outline_w=outline_w)


def draw_six_sparkle(canvas, center, size, fill, outline, outline_w):
    pts = star_path(*center, size=size, points=6, inner_ratio=0.35)
    polygon_stroked(canvas, pts, fill=fill,
                    outline=outline, outline_w=outline_w)


def draw_paw(canvas, center, size, fill):
    pad, toes = paw_print_shapes(*center, size=size)
    canvas.ellipse(pad, fill=fill)
    for toe in toes:
        canvas.ellipse(toe, fill=fill)


def draw_blush(canvas, center, w, h, fill, hatch=True):
    cx, cy = center
    bbox = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    canvas.ellipse(bbox, fill=fill)
    if hatch:
        darker = tuple(max(0, c - 25) for c in fill[:3]) + (
            fill[3] if len(fill) == 4 else 255,)
        line_w = max(1, int(h * 0.08))
        for i in range(-1, 2):
            ox = i * w * 0.22
            canvas.line(
                [(cx - w * 0.28 + ox, cy + h * 0.18),
                 (cx + w * 0.16 + ox, cy - h * 0.20)],
                fill=darker, width=line_w,
            )


def draw_action_lines(canvas, anchor, count, length, color, stroke_w,
                      spread_deg=35, base_angle=0, min_radius=2):
    cx, cy = anchor
    if count <= 1:
        angles = [base_angle]
    else:
        angles = [
            base_angle + (i / (count - 1)) * spread_deg - spread_deg / 2
            for i in range(count)
        ]
    for a in angles:
        r = math.radians(a)
        x0 = cx + min_radius * math.cos(r)
        y0 = cy + min_radius * math.sin(r)
        x1 = cx + (min_radius + length) * math.cos(r)
        y1 = cy + (min_radius + length) * math.sin(r)
        canvas.line([(x0, y0), (x1, y1)], fill=color, width=stroke_w)


def _text_size(canvas, text, font):
    bb = canvas.textbbox((0, 0), text or " ", font=font)
    return (bb[2] - bb[0], bb[3] - bb[1], bb[0], bb[1])


def draw_speech_bubble(canvas, anchor_xy, text, font, fill_color,
                       stroke_color, stroke_w, padding,
                       tail_dir="bottom-left"):
    tw, th, tox, toy = _text_size(canvas, text, font)
    bx, by = anchor_xy
    rect = (bx, by, bx + tw + padding * 2, by + th + padding * 2)
    radius = min(24, int(th / 2 + padding * 0.5))
    canvas.rounded_rectangle(rect, radius=radius, fill=fill_color,
                             outline=stroke_color, width=stroke_w)
    if text:
        canvas.text((bx + padding - tox, by + padding - toy),
                    text, font=font, fill=stroke_color)
    tx0, ty0, tx1, ty1 = rect
    tail_size = max(10, int(th * 0.45))
    if tail_dir == "bottom-right":
        ax = tx1 - tail_size * 1.8
        tail = [(ax, ty1 - 1), (ax + tail_size, ty1 - 1),
                (ax + tail_size * 1.6, ty1 + tail_size)]
    elif tail_dir == "bottom-left":
        ax = tx0 + tail_size * 0.5
        tail = [(ax, ty1 - 1), (ax + tail_size, ty1 - 1),
                (ax - tail_size * 0.4, ty1 + tail_size)]
    elif tail_dir == "top-right":
        ax = tx1 - tail_size * 1.8
        tail = [(ax, ty0 + 1), (ax + tail_size, ty0 + 1),
                (ax + tail_size * 1.6, ty0 - tail_size)]
    else:  # top-left
        ax = tx0 + tail_size * 0.5
        tail = [(ax, ty0 + 1), (ax + tail_size, ty0 + 1),
                (ax - tail_size * 0.4, ty0 - tail_size)]
    canvas.polygon(tail, fill=fill_color, outline=stroke_color)
    if stroke_w > 0:
        loop = list(tail) + [tail[0]]
        canvas.line(loop, fill=stroke_color, width=stroke_w, joint="curve")
    return rect


def draw_burst_bubble(canvas, center, text, font, fill_color,
                      stroke_color, stroke_w, jag_count=14,
                      inner_ratio=0.78, padding=20):
    """Comic starburst caption bubble — multi-line OK."""
    lines = (text or "").split("\n")
    line_metrics = [_text_size(canvas, ln, font) for ln in lines]
    tw = max((m[0] for m in line_metrics), default=10)
    th_each = max((m[1] for m in line_metrics), default=10)
    total_th = th_each * len(lines) + max(0, (len(lines) - 1)) * int(th_each * 0.25)
    cx, cy = center
    rx_outer = tw / 2 + padding
    ry_outer = total_th / 2 + padding
    rx_inner = rx_outer * inner_ratio
    ry_inner = ry_outer * inner_ratio
    pts = []
    for i in range(jag_count * 2):
        a = -math.pi / 2 + i * math.pi / jag_count
        rx = rx_outer if i % 2 == 0 else rx_inner
        ry = ry_outer if i % 2 == 0 else ry_inner
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    canvas.polygon(pts, fill=fill_color, outline=stroke_color)
    if stroke_w > 0:
        loop = list(pts) + [pts[0]]
        canvas.line(loop, fill=stroke_color, width=stroke_w, joint="curve")
    if text:
        cur_y = cy - total_th / 2
        for i, ln in enumerate(lines):
            lw, lh, lox, loy = line_metrics[i]
            canvas.text((cx - lw / 2 - lox, cur_y - loy),
                        ln, font=font, fill=stroke_color)
            cur_y += lh + int(th_each * 0.25)
    return pts


# ──────────────────────────────────────────────────────────────────
# Subject inference & placement zones
# ──────────────────────────────────────────────────────────────────

@dataclass
class Zones:
    full: tuple
    subject: tuple
    top: tuple
    left: tuple
    right: tuple
    bottom: tuple
    floor: tuple
    caption_areas: dict


def heuristic_subject_bbox(W, H, hint=None):
    """Default: center 70% width × 65% height, slight upper bias."""
    if hint and len(hint) == 4:
        return tuple(hint)
    return (int(W * 0.15), int(H * 0.20),
            int(W * 0.85), int(H * 0.85))


def compute_zones(image_size, subject_bbox) -> Zones:
    W, H = image_size
    sx0, sy0, sx1, sy1 = subject_bbox
    return Zones(
        full=(0, 0, W, H),
        subject=subject_bbox,
        top=(0, 0, W, max(0, sy0)),
        left=(0, sy0, max(0, sx0), sy1),
        right=(min(W, sx1), sy0, W, sy1),
        bottom=(0, min(H, sy1), W, H),
        floor=(0, int(H * 0.84), W, H),
        caption_areas={
            "lower-left":  (int(W * 0.04), int(H * 0.62),
                            int(W * 0.50), int(H * 0.95)),
            "lower-right": (int(W * 0.50), int(H * 0.62),
                            int(W * 0.96), int(H * 0.95)),
            "upper-left":  (int(W * 0.04), int(H * 0.04),
                            int(W * 0.50), int(H * 0.22)),
            "upper-right": (int(W * 0.50), int(H * 0.04),
                            int(W * 0.96), int(H * 0.22)),
        },
    )


def random_point_in_rect(rect, rng, inset=0):
    x0, y0, x1, y1 = rect
    if x1 - x0 < 2 or y1 - y0 < 2:
        return ((x0 + x1) / 2, (y0 + y1) / 2)
    return (rng.uniform(x0 + inset, max(x0 + inset + 1, x1 - inset)),
            rng.uniform(y0 + inset, max(y0 + inset + 1, y1 - inset)))


def points_in_negative_zones(zones: Zones, rng, count: int,
                             prefer_top=True) -> list:
    weights = [
        (zones.top,    3 if prefer_top else 2),
        (zones.left,   2),
        (zones.right,  2),
        (zones.bottom, 1),
    ]
    candidates = []
    for rect, w in weights:
        x0, y0, x1, y1 = rect
        if x1 - x0 > 12 and y1 - y0 > 12:
            for _ in range(w):
                candidates.append(rect)
    if not candidates:
        candidates = [zones.full]
    return [random_point_in_rect(rng.choice(candidates), rng)
            for _ in range(count)]


def points_in_floor(zones: Zones, rng, count: int) -> list:
    return [random_point_in_rect(zones.floor, rng) for _ in range(count)]


# ──────────────────────────────────────────────────────────────────
# Config + pool helpers
# ──────────────────────────────────────────────────────────────────

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def hex_to_rgba(s, alpha=255):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def pool_pick(pools: dict, kind: str, mood: str, rng) -> Optional[str]:
    section = pools.get(kind, {})
    candidates = section.get(mood) or section.get("default") or []
    if not candidates:
        return None
    return rng.choice(candidates)


# ──────────────────────────────────────────────────────────────────
# Mood inference (placeholder — heuristics could be added later)
# ──────────────────────────────────────────────────────────────────

def infer_mood(image: Image.Image, default="affectionate") -> str:
    return default


# ──────────────────────────────────────────────────────────────────
# Main decoration logic
# ──────────────────────────────────────────────────────────────────

def decorate(
    image: Image.Image,
    config: dict,
    pools: dict,
    *,
    mood: str = "affectionate",
    mode: str = "playful",
    main_caption: Optional[str] = None,
    reaction_text: Optional[str] = None,
    caption_pos: str = "lower-left",
    subject_hint: Optional[tuple] = None,
    face_points: Optional[list] = None,
    seed: Optional[int] = None,
    font_path: Optional[Path] = None,
    skip_text: bool = False,
) -> Image.Image:
    rng = random.Random(seed)
    img = image.convert("RGBA")
    W, H = img.size

    palette = {k: hex_to_rgba(v) for k, v in config["palette"].items()}
    sizes = config["sizes"]
    outlines = config["outlines"]

    mode_cfg = config["modes"].get(mode, config["modes"]["playful"])
    densities = mode_cfg["density"]
    mood_bias = config.get("mood_bias", {}).get(mood, {})

    def count(name):
        base = densities.get(name, 0)
        bias = mood_bias.get(name, 1.0)
        return max(0, int(round(base * bias)))

    if face_points is None:
        face_points = config.get("face_points") or []

    subject_bbox = heuristic_subject_bbox(W, H, hint=subject_hint)
    zones = compute_zones((W, H), subject_bbox)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    canvas = ImageDraw.Draw(overlay)

    cap_font = load_font(font_path, max(12, int(H * sizes["caption_font_pct"] / 100)))
    rx_font  = load_font(font_path, max(10, int(H * sizes["reaction_font_pct"] / 100)))
    mc_font  = load_font(font_path, max(8,  int(H * sizes["micro_font_pct"] / 100)))

    # ── 1. halo (above head) ─────────────────────────────────────
    if count("halo") > 0:
        sx0, sy0, sx1, sy1 = subject_bbox
        halo_w = (sx1 - sx0) * sizes["halo_w_ratio"]
        halo_h = halo_w * 0.32
        # honor explicit halo_center from config (set via --halo-center)
        hc = config.get("halo_center")
        if hc and len(hc) == 2:
            head_cx, halo_cy = hc[0], hc[1]
        else:
            head_cx = (sx0 + sx1) / 2
            head_top = sy0
            halo_cy = max(halo_h * 0.7, head_top - halo_h * 0.4)
        draw_halo(canvas, (head_cx, halo_cy), halo_w, halo_h,
                  color=palette["halo"], stroke_w=outlines["halo_stroke"])

    # ── 2. blush (only if face_points hinted) ────────────────────
    n_blush = count("blush")
    if n_blush > 0 and face_points:
        bw = sizes["blush_w_ratio"] * (subject_bbox[2] - subject_bbox[0])
        bh = bw * 0.55
        for fp in face_points[: n_blush * 2]:
            draw_blush(canvas, tuple(fp), bw, bh,
                       fill=palette["blush"], hatch=True)

    # ── 3. hearts ───────────────────────────────────────────────
    n_hearts = count("hearts")
    if n_hearts > 0:
        pts = points_in_negative_zones(zones, rng, n_hearts, prefer_top=True)
        for pt in pts:
            size = rng.uniform(sizes["heart_min_pct"], sizes["heart_max_pct"]) * H / 100
            rot = rng.uniform(-25, 25)
            if rng.random() < 0.65:
                fill = palette["heart_pink"]
                outline = palette["outline"]
            else:
                fill = palette["bubble_fill"]
                outline = palette["heart_pink"]
            draw_heart(canvas, pt, size,
                       fill=fill, outline=outline,
                       outline_w=outlines["heart_stroke"], rotation=rot)

    # ── 4. sparkles ─────────────────────────────────────────────
    n_sparkles = count("sparkles")
    if n_sparkles > 0:
        pts = points_in_negative_zones(zones, rng, n_sparkles, prefer_top=True)
        for pt in pts:
            size = rng.uniform(sizes["sparkle_min_pct"],
                               sizes["sparkle_max_pct"]) * H / 100
            r = rng.random()
            if r < 0.65:
                fill = palette["sparkle_white"]
            elif r < 0.9:
                fill = palette["sparkle_yellow"]
            else:
                fill = palette.get("accent_lavender", palette["sparkle_white"])
            if rng.random() < 0.25:
                draw_six_sparkle(canvas, pt, size, fill=fill,
                                 outline=palette["outline"],
                                 outline_w=outlines["sparkle_stroke"])
            else:
                draw_sparkle(canvas, pt, size, fill=fill,
                             outline=palette["outline"],
                             outline_w=outlines["sparkle_stroke"])

    # ── 5. paw prints (floor strip only) ────────────────────────
    n_paws = count("paw_prints")
    if n_paws > 0:
        pts = points_in_floor(zones, rng, n_paws)
        for pt in pts:
            size = rng.uniform(sizes["paw_min_pct"],
                               sizes["paw_max_pct"]) * H / 100
            draw_paw(canvas, pt, size, fill=palette["paw"])

    # ── 6. action lines (around subject edges) ──────────────────
    n_actions = count("action_lines")
    if n_actions > 0:
        sx0, sy0, sx1, sy1 = subject_bbox
        cx = (sx0 + sx1) / 2
        cy = (sy0 + sy1) / 2
        rx_axis = (sx1 - sx0) * 0.55
        ry_axis = (sy1 - sy0) * 0.55
        for _ in range(n_actions):
            angle = rng.uniform(0, 360)
            r = math.radians(angle)
            ax = cx + math.cos(r) * rx_axis
            ay = cy + math.sin(r) * ry_axis
            length = rng.uniform(sizes["action_min_pct"],
                                 sizes["action_max_pct"]) * H / 100
            lines_count = rng.randint(2, 4)
            draw_action_lines(canvas, (ax, ay),
                              count=lines_count, length=length,
                              color=palette["outline"],
                              stroke_w=outlines["action_stroke"],
                              spread_deg=32, base_angle=angle, min_radius=2)

    # ── 7. small reaction speech bubble (upper-right of subject) ─
    rx_text = reaction_text
    if rx_text is None and not skip_text and densities.get("small_reaction", 0) > 0:
        rx_text = pool_pick(pools, "reaction_text", mood, rng)
    if rx_text and not skip_text:
        sx0, sy0, sx1, sy1 = subject_bbox
        anchor = (sx1 - int((sx1 - sx0) * 0.10),
                  sy0 + int((sy1 - sy0) * 0.18))
        # clip anchor inside canvas
        anchor = (min(W - 80, max(0, anchor[0])),
                  min(H - 80, max(0, anchor[1])))
        draw_speech_bubble(canvas, anchor, rx_text, rx_font,
                           fill_color=palette["bubble_fill"],
                           stroke_color=palette["outline"],
                           stroke_w=outlines["bubble_stroke"],
                           padding=int(H * 0.012),
                           tail_dir="bottom-left")

    # ── 8. main caption — starburst comic bubble ────────────────
    cap_text = main_caption
    if cap_text is None and not skip_text and densities.get("main_caption", 0) > 0:
        cap_text = pool_pick(pools, "main_caption", mood, rng)
    if cap_text and not skip_text:
        area = zones.caption_areas.get(caption_pos, zones.caption_areas["lower-left"])
        ax0, ay0, ax1, ay1 = area
        center = ((ax0 + ax1) / 2, (ay0 + ay1) / 2)
        # auto-wrap long captions: split on space at midpoint
        if len(cap_text) > 7 and "\n" not in cap_text:
            words = cap_text.split(" ")
            if len(words) >= 2:
                mid = len(words) // 2
                cap_text = " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
        draw_burst_bubble(canvas, center, cap_text, cap_font,
                          fill_color=palette["bubble_fill"],
                          stroke_color=palette["outline"],
                          stroke_w=outlines["bubble_stroke"],
                          jag_count=14, inner_ratio=0.78,
                          padding=int(H * 0.018))

    # ── 9. micro reaction text (decorative flair) ───────────────
    n_micro = count("micro_text")
    if n_micro > 0 and not skip_text:
        pts = points_in_negative_zones(zones, rng, n_micro, prefer_top=False)
        for pt in pts:
            txt = pool_pick(pools, "micro_text", mood, rng)
            if not txt:
                break
            canvas.text(pt, txt, font=mc_font, fill=palette["outline"])

    # ── 10. emphasis marks (!, ♡, ✦) ────────────────────────────
    n_emph = count("emphasis")
    if n_emph > 0:
        emph_pool = ["!", "!!", "♡", "♥", "✦", "?!"]
        pts = points_in_negative_zones(zones, rng, n_emph, prefer_top=True)
        for pt in pts:
            ch = rng.choice(emph_pool)
            color = palette["heart_pink"] if ch in ("♡", "♥") else palette["outline"]
            canvas.text(pt, ch, font=cap_font, fill=color)

    return Image.alpha_composite(img, overlay)


# ──────────────────────────────────────────────────────────────────
# Vertical 9:16 helper
# ──────────────────────────────────────────────────────────────────

def to_vertical_9x16(image: Image.Image,
                     target: tuple = VERTICAL_TARGET,
                     bg_color=(255, 255, 255, 255)) -> Image.Image:
    tw, th = target
    img = image.convert("RGBA")
    iw, ih = img.size
    scale = min(tw / iw, th / ih)
    new_w = max(1, int(iw * scale))
    new_h = max(1, int(ih * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    bg = Image.new("RGBA", (tw, th), bg_color)
    x = (tw - new_w) // 2
    y = (th - new_h) // 2
    bg.alpha_composite(resized, (x, y))
    return bg


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

def parse_bbox(s):
    if not s:
        return None
    return tuple(int(x) for x in s.split(","))


def parse_points(s):
    if not s:
        return None
    return [tuple(int(x) for x in p.split(",")) for p in s.split(";")]


def parse_args():
    p = argparse.ArgumentParser(
        description="Rule-based webtoon overlay decorator for pet photos.")
    p.add_argument("--image", required=True, help="Input photo path.")
    p.add_argument("--mood", default=None, choices=MOOD_CHOICES,
                   help="Sticker selection bias. Default: inferred from image.")
    p.add_argument("--mode", default="playful", choices=MODE_CHOICES,
                   help="Sticker density preset.")
    p.add_argument("--caption", default=None,
                   help="Override main caption (skip pool sampling).")
    p.add_argument("--reaction", default=None,
                   help="Override small reaction text.")
    p.add_argument("--caption-pos", default="lower-left",
                   choices=CAPTION_POSITIONS)
    p.add_argument("--subject", default=None,
                   help="Subject bbox override 'x0,y0,x1,y1' (pixels).")
    p.add_argument("--face-points", default=None,
                   help="Cheek points for blush 'x,y;x,y;...' (optional).")
    p.add_argument("--halo-center", default=None,
                   help="Halo center 'x,y' (pixels). Default: top-center of subject bbox.")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed (pin layout).")
    p.add_argument("--font", default=None, help="Korean TTF path override.")
    p.add_argument("--out", default=None,
                   help="Main output path. Default: output/decorated.png")
    p.add_argument("--no-vertical", action="store_true",
                   help="Skip 9:16 export.")
    p.add_argument("--no-text", action="store_true",
                   help="Visual-only (no captions/reaction text).")
    p.add_argument("--config", default=None,
                   help="Override style_config.json path.")
    p.add_argument("--pools", default=None,
                   help="Override text_pools.json path.")
    return p.parse_args()


def main():
    args = parse_args()

    config_path = Path(args.config) if args.config else CONFIG_PATH
    pools_path = Path(args.pools) if args.pools else POOLS_PATH
    if not config_path.exists():
        print(f"Missing config: {config_path}", file=sys.stderr)
        sys.exit(2)
    if not pools_path.exists():
        print(f"Missing pools: {pools_path}", file=sys.stderr)
        sys.exit(2)
    config = load_json(config_path)
    pools = load_json(pools_path)

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"Image not found: {img_path}", file=sys.stderr)
        sys.exit(2)
    img = Image.open(img_path)
    img = ImageOps.exif_transpose(img)

    font_path = Path(args.font) if args.font else find_korean_font(FONT_DIR)
    if font_path is None and not args.no_text:
        print("WARN: no Korean font detected. Drop a TTF in "
              "decorate_photo/fonts/ or pass --font. Falling back.",
              file=sys.stderr)

    mood = args.mood or infer_mood(img)
    subject_hint = parse_bbox(args.subject)
    face_points = parse_points(args.face_points)
    if args.halo_center:
        config["halo_center"] = tuple(int(x) for x in args.halo_center.split(","))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out = decorate(
        img, config, pools,
        mood=mood, mode=args.mode,
        main_caption=args.caption,
        reaction_text=args.reaction,
        caption_pos=args.caption_pos,
        subject_hint=subject_hint,
        face_points=face_points,
        seed=args.seed,
        font_path=font_path,
        skip_text=args.no_text,
    )

    main_out = Path(args.out) if args.out else (OUT_DIR / "decorated.png")
    main_out.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(main_out, "PNG")
    print(f"Saved: {main_out}")

    if not args.no_vertical:
        vert = to_vertical_9x16(out)
        vert_out = main_out.parent / "decorated_vertical.png"
        vert.convert("RGB").save(vert_out, "PNG")
        print(f"Saved: {vert_out}")


if __name__ == "__main__":
    main()
