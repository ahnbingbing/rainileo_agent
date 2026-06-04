"""
scripts/render_episode_1.py — Episode 1 final render

Pipeline
--------
1. Resolve 8 source assets (photos + iPhone movs) and 3 BGM tracks from
   data/agent.db. Photo paths in the DB are mac-absolute; rebase them to
   the project root so this script runs in either macOS or sandbox.
2. Pre-convert HEIC photos to JPEG via the best available decoder
   (sips on macOS, heif-convert on Linux, or ffmpeg if compiled with
   libheif). Skips with a warning if no decoder is available.
3. Render each cut to a 1080x1920 H.264 segment:
     - portrait photo: ken-burns slow zoom-in
     - landscape photo: blur-bg letterbox + slight zoom
     - portrait video (iPhone rotate=90 metadata): trim + scale
     - landscape video: blur-bg letterbox + center crop
   Captions (KR top + EN bottom) are rendered as a transparent PNG
   strip via PIL, then overlaid in the bottom safe area with a 0.3s
   fade-in.
4. Concat all segments with the concat demuxer.
5. Build the BGM track separately — single source, trimmed to 30s
   with a soft fade-in (0.6s) and fade-out (1.2s):
     playdate (warm/mid, banjo + ukulele) — covers the whole ep
6. Mux the concatenated video and BGM audio into the final mp4.

Usage
-----
    cd ~/code/rianileo-agent
    python -m scripts.render_episode_1                  # full render
    python -m scripts.render_episode_1 --skip-heic      # sandbox dry-run
    python -m scripts.render_episode_1 --keep-tmp       # keep tmp segments
    python -m scripts.render_episode_1 --out my.mp4     # custom output

Output
------
    data/output/episode_1.mp4   (1080x1920, 30fps, ~30s)
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
OUT_DIR = ROOT / "data" / "output"
TMP = ROOT / "data" / "tmp" / "ep1"
log = logging.getLogger("render_ep1")

# ─────────────────────────────────────────────────────────────────────
# Storyboard
# ─────────────────────────────────────────────────────────────────────
# The sticker system lives in scripts.sticker_scatter — auto-discovers
# PNGs in assets/stickers/<category>/, scatters them across each cut,
# and supports precisely-pinned label stickers for the solo intros.
sys.path.insert(0, str(ROOT / "scripts"))
from sticker_scatter import (  # noqa: E402  (legacy single-PNG path, kept for compat)
    FixedSticker, StickerPack,
    available_stickers, find_sticker,
    render_fixed_sticker, render_scatter,
)
# NEW: per-frame animated decoration (dense kawaii style)
from deco_anim import (  # noqa: E402
    AnimSticker, DecoScene, render_frames as render_deco_frames,
    halo_ring, face_accents, body_hearts, corner_sparkles,
    edge_ribbon, burst_at, available_stickers as anim_stickers,
)
import random as _random  # noqa: E402


@dataclass
class Cut:
    idx: int
    asset_id: str
    dur: float
    kr: str
    en: str
    video_start: float = 0.0
    fixed_stickers: list[FixedSticker] = field(default_factory=list)
    scatter: StickerPack | None = None
    notes: str = ""
    # True 면 정지 사진 대신 scripts/animate_hero.py 로 미리 생성한 mp4
    # (data/output/animated/<asset_id>__*.mp4) 를 사용한다.
    use_animated: bool = False
    # NEW: dense animated decoration. If set, render_deco_frames_for_cut
    # builds a PNG sequence and the segment renderer overlays it. The
    # legacy `scatter` / `fixed_stickers` path is bypassed.
    recipe: str | None = None
    # Subject head position in normalized (x, y) ∈ [0,1] AFTER 9:16 crop.
    # Used by recipes that need a head anchor (halo, face_accents).
    subject_center: tuple[float, float] | None = None
    # Secondary subject (for together cuts with two pets in frame).
    subject_center_b: tuple[float, float] | None = None


def _label_sticker(sticker_path: Path | None,
                   x_pct: float, y_pct: float,
                   label: str, color_hex: str,
                   size: int = 280) -> list[FixedSticker]:
    """Build a list with one FixedSticker if the source PNG exists."""
    if sticker_path is None:
        return []
    return [FixedSticker(sticker_path, x_pct, y_pct, size=size,
                         label=label, label_color=color_hex)]


# Resolved at module load — these point at concrete PNGs in assets/stickers/
# Naming convention: hearts_ryani_ai_*  → Ryani-themed (pink), hearts_leo_ai_* → Leo (gold/orange).
_PINK_HEART   = find_sticker("hearts", "ryani") or find_sticker("hearts", "pink")
_GOLD_HEART   = find_sticker("hearts", "leo")   or find_sticker("hearts", "gold")


CUTS: list[Cut] = [
    # 5-cut emotional arc, each with a dense DecoScene recipe. The new
    # decoration system renders a PNG-per-frame sequence with halos,
    # face accents, body hearts, edge ribbons, corner sparkles, pastel
    # corner glow, and tiny background twinkles — driven by `recipe`.
    # Cut 1 — HOOK / Ryani solo. Cuteness overload from the very first beat.
    Cut(1, "med_2026_05_06_203421_icloud_331110de", 3.0,
        "오늘도 귀여움 과다", "Cuteness overload",
        recipe="ryani_hook",
        subject_center=(0.23, 0.40),
        use_animated=True),    # Sora i2v: gentle blink / head tilt
    # Cut 2 — Leo solo. "Leo's turn" energy — playful intro.
    Cut(2, "med_2026_05_06_203433_icloud_57e3500d", 3.0,
        "레오도 질 수 없지", "Leo's turn",
        recipe="leo_intro",
        subject_center=(0.66, 0.55),
        use_animated=True),    # Sora i2v: gentle blink / head tilt
    # Cut 3 — Together, playing. Both pets in frame, hearts everywhere.
    Cut(3, "med_2025_12_14_152903_icloud_ad7fb05a", 3.5,
        "둘이 있으면 더 귀여움", "Better together",
        recipe="together_play",
        subject_center=(0.40, 0.50),    # Ryani
        subject_center_b=(0.62, 0.55)), # Leo
    # Cut 4 — Together, warm/calm. Settled, "best buds" beat.
    Cut(4, "med_2026_02_07_111144_icloud_77fa65d8", 3.0,
        "이젠 단짝", "Best buds",
        recipe="together_warm",
        subject_center=(0.42, 0.48),
        subject_center_b=(0.60, 0.52)),
    # Cut 5 — Highlight close / today's cuteness complete.
    Cut(5, "med_2025_12_12_193926_icloud_6a1268c0", 3.0,
        "오늘의 귀여움 완료", "Today's cuteness: complete",
        recipe="closer",
        subject_center=(0.50, 0.45)),
]

# Banner intro/outro frames (channel banner letterboxed to 1080x1920).
# Trimmed to 1.5s each — punchy, doesn't bury the body cuts.
BANNER_PNG = ROOT / "data" / "output" / "capcut_package" / "banner_card_1080x1920.png"
BANNER_INTRO_DUR = 1.5
BANNER_OUTRO_DUR = 1.5

# Single-track BGM — playdate (warm/mid, banjo+ukulele friendship vibe)
BGM_FILE = "geoffharvey-playdate-427890.mp3"
BGM_START = 8.0     # skip the intro of the source so we land in the groove
BGM_FADE_IN = 1.0   # gentle entry — was 0.6 (felt snappy)
BGM_FADE_OUT = 3.0  # graceful exit — was 1.2 (felt cut off)
BGM_VOLUME = 0.8
BODY_DUR = sum(c.dur for c in CUTS)             # 30.0  (cuts only)
TOTAL_DUR = BANNER_INTRO_DUR + BODY_DUR + BANNER_OUTRO_DUR   # 36.0

CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30
CRF = 18
PRESET = "fast"

# ─────────────────────────────────────────────────────────────────────
# Path & decoder helpers
# ─────────────────────────────────────────────────────────────────────
def resolve(p: str | Path) -> Path:
    """Map mac-absolute or project-relative paths to the current ROOT."""
    p = Path(p)
    if p.is_absolute() and "rianileo-agent" in p.parts:
        idx = p.parts.index("rianileo-agent")
        return (ROOT / Path(*p.parts[idx + 1:])).resolve()
    if not p.is_absolute():
        return (ROOT / p).resolve()
    return p


def heic_to_jpeg(src: Path, dst: Path) -> bool:
    """Best-effort HEIC -> JPEG. Returns True on success."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("sips"):  # macOS built-in
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
            capture_output=True,
        )
        if r.returncode == 0 and dst.exists():
            return True
    if shutil.which("heif-convert"):
        r = subprocess.run(
            ["heif-convert", "-q", "92", str(src), str(dst)],
            capture_output=True,
        )
        if r.returncode == 0 and dst.exists():
            return True
    # try ffmpeg with libheif (modern Homebrew builds support this)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-q:v", "2", str(dst)],
        capture_output=True,
    )
    if r.returncode == 0 and dst.exists():
        return True
    log.warning("no HEIC decoder available for %s", src)
    return False


def normalize_photo(src: Path, dst: Path) -> Path:
    """Apply EXIF orientation and re-save as upright JPEG.

    iPhone JPEGs are often stored physically landscape with an EXIF
    Orientation=6 tag. ffmpeg's image2 demuxer does not honour EXIF
    rotation, so we bake it in here once via Pillow.
    """
    from PIL import Image, ImageOps
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=92, optimize=True)
    return dst


def find_font(candidates: list[str]) -> str | None:
    """Return the first existing font path."""
    for c in candidates:
        p = Path(c)
        if p.exists():
            return str(p)
    return None


# ─────────────────────────────────────────────────────────────────────
# Decoration rendering (PIL → transparent 1080x1920 PNG)
# ─────────────────────────────────────────────────────────────────────
KR_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]
EN_FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# ─────────────────────────────────────────────────────────────────────
# Per-frame DecoScene factory & renderer
# ─────────────────────────────────────────────────────────────────────
def make_scene_for_cut(cut: Cut) -> DecoScene:
    """Build a DecoScene from a Cut.recipe name.

    Recipes are dense kawaii layouts: halo + face accents + body hearts +
    edge ribbon + corner sparkles + pastel corner glow + bg twinkles. Each
    cut gets a deterministic seed so re-renders are stable.
    """
    rng = _random.Random(1000 + cut.idx * 7)
    lib = anim_stickers()
    stickers: list[AnimSticker] = []

    recipe = cut.recipe or "default"
    subj = cut.subject_center or (0.50, 0.45)
    subj_b = cut.subject_center_b

    if recipe == "ryani_hook":
        # HOOK: instant, dense, magical. Halo, face heart, body hearts,
        # edge ribbon, corner sparkles. Glow strong, twinkles many.
        stickers += corner_sparkles(rng, library=lib, base_t=0.00)
        stickers += halo_ring(rng, subj,
                              radius_pct=0.11, count=8,
                              arc_deg=(200, 340),
                              size_range=(65, 100),
                              library=lib, base_t=0.00,
                              float_amp_px=12, wobble_amp_deg=7)
        stickers += face_accents(rng, subj, library=lib, base_t=0.10)
        stickers += body_hearts(rng, subj, count=6,
                                spread_pct=(0.18, 0.28),
                                library=lib, base_t=0.30, stagger_t=0.10)
        stickers += edge_ribbon(rng, count_top=3, count_bottom=2,
                                library=lib, base_t=0.40)
        return DecoScene(
            duration_sec=cut.dur, fps=FPS, stickers=stickers,
            caption_kr=cut.kr, caption_en=cut.en,
            glow_strength=0.55, glow_color_rgb=(255, 192, 215),
            glow_corners=("tl", "tr", "bl", "br"),
            bg_sparkle_count=18, bg_sparkle_seed=1001,
        )

    if recipe == "leo_intro":
        # Leo's turn — slightly more orange/playful. Halo + face + body hearts.
        stickers += corner_sparkles(rng, library=lib, base_t=0.00)
        stickers += halo_ring(rng, subj,
                              radius_pct=0.11, count=8,
                              arc_deg=(200, 340),
                              size_range=(65, 100),
                              library=lib, base_t=0.00,
                              float_amp_px=12, wobble_amp_deg=7)
        stickers += face_accents(rng, subj, library=lib, base_t=0.10)
        stickers += body_hearts(rng, subj, count=6,
                                spread_pct=(0.18, 0.28),
                                library=lib, base_t=0.30, stagger_t=0.10)
        stickers += edge_ribbon(rng, count_top=3, count_bottom=2,
                                library=lib, base_t=0.40)
        return DecoScene(
            duration_sec=cut.dur, fps=FPS, stickers=stickers,
            caption_kr=cut.kr, caption_en=cut.en,
            glow_strength=0.50, glow_color_rgb=(255, 215, 185),  # soft peach
            glow_corners=("tl", "tr", "bl", "br"),
            bg_sparkle_count=18, bg_sparkle_seed=1002,
        )

    if recipe == "together_play":
        # Two pets in frame — hearts AROUND BOTH, no halo (would crowd faces).
        # Heart burst between them to read as "closeness".
        stickers += corner_sparkles(rng, library=lib, base_t=0.00)
        stickers += body_hearts(rng, subj, count=4,
                                spread_pct=(0.14, 0.22),
                                library=lib, base_t=0.05, stagger_t=0.08)
        if subj_b is not None:
            stickers += body_hearts(rng, subj_b, count=4,
                                    spread_pct=(0.14, 0.22),
                                    library=lib, base_t=0.15, stagger_t=0.08)
            # Closeness heart between the two subjects
            mid_x = (subj[0] + subj_b[0]) / 2
            mid_y = (subj[1] + subj_b[1]) / 2 - 0.08
            stickers += burst_at(rng, mid_x, mid_y,
                                 category="hearts", size=170,
                                 appear_t=0.50, pulse_amp=0.12,
                                 library=lib)
        stickers += edge_ribbon(rng, count_top=3, count_bottom=2,
                                library=lib, base_t=0.30)
        return DecoScene(
            duration_sec=cut.dur, fps=FPS, stickers=stickers,
            caption_kr=cut.kr, caption_en=cut.en,
            glow_strength=0.45, glow_color_rgb=(255, 205, 220),
            glow_corners=("tl", "tr", "bl", "br"),
            bg_sparkle_count=14, bg_sparkle_seed=1003,
        )

    if recipe == "together_warm":
        # Calmer beat — fewer stickers, longer floats, halo over the pair.
        center = subj if subj_b is None else (
            (subj[0] + subj_b[0]) / 2, (subj[1] + subj_b[1]) / 2
        )
        stickers += halo_ring(rng, center,
                              radius_pct=0.13, count=7,
                              arc_deg=(205, 335),
                              size_range=(55, 90),
                              library=lib, base_t=0.00,
                              float_amp_px=14, wobble_amp_deg=5)
        stickers += body_hearts(rng, center, count=5,
                                spread_pct=(0.20, 0.28),
                                library=lib, base_t=0.20, stagger_t=0.12)
        stickers += corner_sparkles(rng, library=lib, base_t=0.10)
        return DecoScene(
            duration_sec=cut.dur, fps=FPS, stickers=stickers,
            caption_kr=cut.kr, caption_en=cut.en,
            glow_strength=0.40, glow_color_rgb=(255, 210, 225),
            glow_corners=("tl", "tr", "bl", "br"),
            bg_sparkle_count=12, bg_sparkle_seed=1004,
        )

    if recipe == "closer":
        # Final beat — celebratory, sparkly, hearts settle in last.
        stickers += corner_sparkles(rng, library=lib, base_t=0.00)
        stickers += halo_ring(rng, subj,
                              radius_pct=0.12, count=8,
                              arc_deg=(200, 340),
                              size_range=(60, 95),
                              library=lib, base_t=0.00,
                              float_amp_px=10, wobble_amp_deg=6)
        stickers += body_hearts(rng, subj, count=6,
                                spread_pct=(0.18, 0.28),
                                library=lib, base_t=0.20, stagger_t=0.10)
        stickers += edge_ribbon(rng, count_top=4, count_bottom=3,
                                library=lib, base_t=0.30)
        return DecoScene(
            duration_sec=cut.dur, fps=FPS, stickers=stickers,
            caption_kr=cut.kr, caption_en=cut.en,
            glow_strength=0.55, glow_color_rgb=(255, 200, 220),
            glow_corners=("tl", "tr", "bl", "br"),
            bg_sparkle_count=22, bg_sparkle_seed=1005,
        )

    # Fallback: light edge ribbon + corner sparkles + caption only
    stickers += corner_sparkles(rng, library=lib, base_t=0.00)
    stickers += edge_ribbon(rng, count_top=3, count_bottom=2,
                            library=lib, base_t=0.10)
    return DecoScene(
        duration_sec=cut.dur, fps=FPS, stickers=stickers,
        caption_kr=cut.kr, caption_en=cut.en,
        glow_strength=0.30, glow_color_rgb=(255, 210, 225),
        bg_sparkle_count=8, bg_sparkle_seed=9999,
    )


def render_deco_frames_for_cut(cut: Cut, tmp_dir: Path) -> Path:
    """Render per-frame deco PNG sequence into tmp_dir/cut_NN/.
    Returns the directory containing deco_NNNN.png (1-indexed)."""
    scene = make_scene_for_cut(cut)
    seq_dir = tmp_dir / f"cut_{cut.idx:02d}_deco"
    if seq_dir.exists():
        shutil.rmtree(seq_dir, ignore_errors=True)
    seq_dir.mkdir(parents=True, exist_ok=True)
    n = render_deco_frames(scene, seq_dir, prefix="deco_")
    log.info("[cut %d] deco frames → %s (%d frames, recipe=%s)",
             cut.idx, seq_dir.name, n, cut.recipe or "default")
    return seq_dir


def render_decoration_png(cut: Cut, out_path: Path) -> Path:
    """Build a 1080x1920 transparent PNG with:
       - bottom caption strip (KR + EN)
       - any pinned label stickers (solo intros)
       - a scatter of decorative PNG stickers from assets/stickers/
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

    # ── scatter first so labels & captions stay on top ──
    if cut.scatter is not None:
        # Single keep-out box covering the pet face band AND the bottom
        # caption strip's central text region. Stickers therefore land
        # in the top band (above pets) or in the left/right side strips.
        avoid = (
            int(CANVAS_W * 0.16), int(CANVAS_H * 0.28),
            int(CANVAS_W * 0.84), CANVAS_H,
        )
        pack = cut.scatter.with_avoid_box(*avoid)
        render_scatter(img, pack, cut_idx=cut.idx)

    # ── pinned label stickers ──
    for fs in cut.fixed_stickers:
        render_fixed_sticker(img, fs)

    # ── caption strip at bottom ──
    draw = ImageDraw.Draw(img)
    block_h = 280
    block_y = CANVAS_H - block_h
    for y in range(block_y, CANVAS_H):
        ratio = (y - block_y) / block_h
        a = int(180 * ratio)
        draw.line([(0, y), (CANVAS_W, y)], fill=(0, 0, 0, a))

    kr_font_path = find_font(KR_FONT_CANDIDATES) or find_font(EN_FONT_CANDIDATES)
    en_font_path = find_font(EN_FONT_CANDIDATES) or kr_font_path
    kr_font = (ImageFont.truetype(kr_font_path, 64) if kr_font_path
               else ImageFont.load_default())
    en_font = (ImageFont.truetype(en_font_path, 38) if en_font_path
               else ImageFont.load_default())

    def draw_centered(text, font, y, fill=(255, 255, 255, 255)):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (CANVAS_W - tw) // 2
        draw.text((x + 2, y + 3), text, font=font, fill=(0, 0, 0, 190))
        draw.text((x, y), text, font=font, fill=fill)

    draw_centered(cut.kr, kr_font, block_y + 60)
    draw_centered(cut.en, en_font, block_y + 160, fill=(255, 255, 255, 230))

    img.save(out_path, "PNG")
    return out_path


def render_banner_segment(out: Path, dur: float, *,
                          fade_in: bool = False,
                          fade_out: bool = False) -> None:
    """Convert the banner card PNG into a still 1080x1920 H.264 segment."""
    if not BANNER_PNG.exists():
        # On-demand build via make_banner_card if the canonical card is
        # missing (e.g. fresh checkout). It writes to BANNER_PNG.
        log.info("banner card missing — building via make_banner_card")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_banner_card.py")],
            check=True,
        )

    fades = []
    if fade_in:
        fades.append("fade=t=in:st=0:d=0.5")
    if fade_out:
        fades.append(f"fade=t=out:st={max(0.0, dur-0.5):.2f}:d=0.5")
    vf = ",".join(["scale=1080:1920", "setsar=1", f"fps={FPS}", *fades])
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(FPS), "-t", f"{dur}", "-i", str(BANNER_PNG),
        "-vf", vf,
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        "-pix_fmt", "yuv420p",
        "-an", str(out),
    ]
    log.info("[banner] %s (%.1fs%s%s)", out.name, dur,
             ", fade-in" if fade_in else "",
             ", fade-out" if fade_out else "")
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────────────────────────────
# Segment renderers
# ─────────────────────────────────────────────────────────────────────
def ffprobe_dims(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


def _deco_input_args(deco: Path) -> list[str]:
    """Return the ffmpeg input flags for the deco overlay.

    Supports two modes:
      - per-frame sequence directory:  -framerate FPS -i <dir>/deco_%04d.png
      - legacy single PNG:             -loop 1 -framerate FPS -t DUR -i PNG
    """
    if deco.is_dir():
        # image sequence; let the demuxer pick up deco_NNNN.png (1-indexed)
        return ["-framerate", str(FPS),
                "-i", str(deco / "deco_%04d.png")]
    # single PNG, looped for the duration of the cut
    return ["-loop", "1", "-framerate", str(FPS), "-i", str(deco)]


def render_photo_segment(cut: Cut, src: Path, deco: Path, out: Path) -> None:
    """Photo cut → 1080x1920 H.264 with ken-burns + decoration overlay.

    `deco` may be a single PNG (legacy) OR a directory containing the
    per-frame deco_NNNN.png sequence rendered by render_deco_frames_for_cut.
    """
    try:
        w, h = ffprobe_dims(src)
    except Exception:
        w, h = (4284, 5712)
    is_portrait = h >= w
    dur_frames = max(int(cut.dur * FPS), 2)
    z_expr = f"1+0.06*on/{dur_frames}"

    if is_portrait:
        graph = (
            f"[0:v]scale=1188:2112:force_original_aspect_ratio=increase,"
            f"crop=1188:2112,setsar=1,"
            f"zoompan=z='{z_expr}':d={dur_frames}:s={CANVAS_W}x{CANVAS_H}:fps={FPS}[fg];"
            f"[fg][1:v]overlay=0:0:format=auto:shortest=1[outv]"
        )
    else:
        graph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},gblur=sigma=24,setsar=1[bg];"
            f"[0:v]scale=1188:-2:force_original_aspect_ratio=decrease,setsar=1,"
            f"zoompan=z='{z_expr}':d={dur_frames}:s={CANVAS_W}x{max(int(CANVAS_W*h/w),2)}:fps={FPS}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
            f"[base][1:v]overlay=0:0:format=auto:shortest=1[outv]"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(FPS), "-t", str(cut.dur), "-i", str(src),
        *_deco_input_args(deco),
        "-filter_complex", graph,
        "-map", "[outv]", "-t", str(cut.dur),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        "-an", str(out),
    ]
    log.info("[cut %d] photo → %s (deco=%s)",
             cut.idx, out.name, "seq" if deco.is_dir() else "png")
    subprocess.run(cmd, check=True)


def render_video_segment(cut: Cut, src: Path, deco: Path, out: Path) -> None:
    """Video cut → 1080x1920 with auto-rotate awareness + decoration overlay.

    `deco` may be a single PNG (legacy) OR a directory containing the
    per-frame deco_NNNN.png sequence rendered by render_deco_frames_for_cut.
    """
    try:
        w, h = ffprobe_dims(src)
    except Exception:
        w, h = (1920, 1080)

    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_tags=rotate", "-of", "default=nw=1:nk=1", str(src)],
        capture_output=True, text=True,
    )
    rotate = (r.stdout.strip() or "0")
    auto_portrait = rotate in ("90", "270") or h >= w

    if auto_portrait:
        vchain = (
            f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}"
        )
        graph = (
            f"[0:v]{vchain}[fg];"
            f"[fg][1:v]overlay=0:0:format=auto:shortest=1[outv]"
        )
    else:
        graph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},gblur=sigma=24,setsar=1,fps={FPS}[bg];"
            f"[0:v]scale={CANVAS_W}:-2,setsar=1,fps={FPS}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
            f"[base][1:v]overlay=0:0:format=auto:shortest=1[outv]"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-ss", str(cut.video_start), "-t", str(cut.dur), "-i", str(src),
        *_deco_input_args(deco),
        "-filter_complex", graph,
        "-map", "[outv]", "-t", str(cut.dur),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        "-an", str(out),
    ]
    log.info("[cut %d] video → %s (rotate=%s, deco=%s)",
             cut.idx, out.name, rotate, "seq" if deco.is_dir() else "png")
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────────────────────────────
# BGM
# ─────────────────────────────────────────────────────────────────────
def build_bgm(out_path: Path) -> None:
    """Single-track BGM aligned to total runtime.

    Layout:
        0  ────────  BANNER_INTRO_DUR        silence (banner intro)
        +  ────────  BODY_DUR                music with soft fade in/out
        +  ────────  BANNER_OUTRO_DUR        silence (banner outro)
    """
    src = resolve(Path("assets/bgm") / BGM_FILE)
    fade_out_st = max(0.0, BODY_DUR - BGM_FADE_OUT)
    delay_ms = int(BANNER_INTRO_DUR * 1000)
    chain = (
        f"[0:a]atrim=duration={BODY_DUR},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={BGM_FADE_IN},"
        f"afade=t=out:st={fade_out_st}:d={BGM_FADE_OUT},"
        f"volume={BGM_VOLUME},"
        f"adelay={delay_ms}|{delay_ms},"
        f"apad=whole_dur={TOTAL_DUR},"
        f"aresample=44100[mout]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-ss", str(BGM_START), "-i", str(src),
        "-filter_complex", chain,
        "-map", "[mout]",
        "-c:a", "pcm_s16le", "-ar", "44100",
        str(out_path),
    ]
    log.info("building BGM → %s (%s, body %.1fs in %.1fs total)",
             out_path.name, BGM_FILE, BODY_DUR, TOTAL_DUR)
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def lookup_assets(con: sqlite3.Connection) -> dict[str, dict]:
    ids = [c.asset_id for c in CUTS]
    qmarks = ",".join(["?"] * len(ids))
    rows = con.execute(
        f"SELECT asset_id, kind, file_path FROM assets WHERE asset_id IN ({qmarks})",
        ids,
    ).fetchall()
    return {r[0]: {"kind": r[1], "file_path": r[2]} for r in rows}


def find_animated_clip(asset_id: str) -> Path | None:
    """data/output/animated/ 에서 해당 asset_id 의 가장 최근 mp4 를 찾는다.

    animate_hero.py 가 만드는 출력 패턴: <asset_id>__<YYYYMMDD_HHMMSS>.mp4
    찾지 못하면 None.
    """
    out_dir = ROOT / "data" / "output" / "animated"
    if not out_dir.is_dir():
        return None
    candidates = sorted(
        out_dir.glob(f"{asset_id}__*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render Episode 1.")
    ap.add_argument("--out", default=str(OUT_DIR / "episode_1.mp4"))
    ap.add_argument("--skip-heic", action="store_true",
                    help="skip cuts whose source HEIC cannot be decoded "
                         "(useful for sandbox preview)")
    ap.add_argument("--keep-tmp", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(DB_PATH))
    try:
        assets = lookup_assets(con)
    finally:
        con.close()

    # Pre-flight: missing assets?
    for c in CUTS:
        if c.asset_id not in assets:
            log.error("asset_id not in DB: %s", c.asset_id)
            return 2

    segments: list[Path] = []
    skipped: list[int] = []

    # Banner intro
    intro_seg = TMP / "seg_00_banner_intro.mp4"
    render_banner_segment(intro_seg, BANNER_INTRO_DUR, fade_in=True, fade_out=False)
    segments.append(intro_seg)

    for c in CUTS:
        meta = assets[c.asset_id]
        src = resolve(meta["file_path"])
        if not src.exists():
            log.error("[cut %d] source missing: %s", c.idx, src)
            return 3

        # use_animated 가 켜져 있고 미리 생성된 i2v mp4 가 있으면
        # 정지 사진 대신 그 mp4 를 비디오 컷으로 처리한다.
        effective_kind = meta["kind"]
        work_src = src
        if c.use_animated:
            anim = find_animated_clip(c.asset_id)
            if anim is not None and anim.exists():
                log.info("[cut %d] using animated clip: %s",
                         c.idx, anim.name)
                effective_kind = "video"
                work_src = anim
            else:
                log.warning("[cut %d] use_animated=True but no mp4 found in "
                            "data/output/animated/; falling back to photo",
                            c.idx)

        # Photo normalization: bake EXIF orientation into a fresh JPEG
        # so ffmpeg sees an upright source. HEICs go through a decoder
        # first, then normalize the resulting JPEG in the same pass.
        if effective_kind == "photo":
            heic_jpeg = None
            if src.suffix.lower() in (".heic", ".heif"):
                heic_jpeg = TMP / f"{src.stem}.jpg"
                if not heic_jpeg.exists() and not heic_to_jpeg(src, heic_jpeg):
                    if args.skip_heic:
                        log.warning("[cut %d] skipping (no HEIC decoder)", c.idx)
                        skipped.append(c.idx)
                        continue
                    log.error("[cut %d] HEIC decode failed: %s", c.idx, src)
                    return 4
            normalized = TMP / f"norm_{src.stem}.jpg"
            try:
                normalize_photo(heic_jpeg or src, normalized)
                work_src = normalized
            except Exception as e:
                log.warning("[cut %d] normalize failed (%s); using raw source",
                            c.idx, e)
                work_src = heic_jpeg or src

        # Decoration. Two paths:
        #   1. NEW (preferred): per-frame PNG sequence via deco_anim.
        #      Triggered when cut.recipe is set. Captions, glow, twinkles,
        #      and animated stickers are all baked into the sequence.
        #   2. LEGACY: single 1080x1920 transparent PNG via PIL. Used by
        #      cuts that still rely on scatter / fixed_stickers.
        seg_out = TMP / f"seg_{c.idx:02d}.mp4"
        if c.recipe is not None:
            deco = render_deco_frames_for_cut(c, TMP)
        else:
            deco = TMP / f"deco_{c.idx:02d}.png"
            render_decoration_png(c, deco)

        if effective_kind == "video":
            render_video_segment(c, work_src, deco, seg_out)
        else:
            render_photo_segment(c, work_src, deco, seg_out)
        segments.append(seg_out)

    # Banner outro
    outro_seg = TMP / "seg_99_banner_outro.mp4"
    render_banner_segment(outro_seg, BANNER_OUTRO_DUR, fade_in=False, fade_out=True)
    segments.append(outro_seg)

    if len(segments) <= 2:
        log.error("only banner segments produced — no body cuts. cannot render.")
        return 5

    # Concat segments
    list_path = TMP / "concat.txt"
    list_path.write_text("\n".join(f"file '{p}'" for p in segments) + "\n")
    silent_video = TMP / "video_only.mp4"
    log.info("concatenating %d segments → %s", len(segments), silent_video.name)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
         "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", str(silent_video)],
        check=True,
    )

    # BGM build (full 30s even when video is shorter due to skipped cuts)
    bgm_audio = TMP / "bgm.wav"
    build_bgm(bgm_audio)

    # Determine actual video length to avoid audio overrun
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(silent_video)],
        capture_output=True, text=True, check=True,
    )
    video_len = float(r.stdout.strip())

    # Final mux + soft tail fade on audio
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("muxing → %s", out_path)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
         "-i", str(silent_video), "-i", str(bgm_audio),
         "-filter_complex",
         f"[1:a]atrim=duration={video_len:.3f},"
         f"afade=t=in:st=0:d=0.4,"
         f"afade=t=out:st={max(0, video_len-0.6):.3f}:d=0.6,"
         f"volume=0.85[aout]",
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(out_path)],
        check=True,
    )

    if not args.keep_tmp:
        shutil.rmtree(TMP, ignore_errors=True)

    print()
    print(f"  ✓ rendered: {out_path}")
    print(f"  cuts: {len(segments)} of {len(CUTS)}"
          + (f" (skipped: {skipped})" if skipped else ""))
    print(f"  duration: {video_len:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
