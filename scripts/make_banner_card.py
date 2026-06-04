"""
scripts/make_banner_card.py
---------------------------
Build a 1080x1920 vertical banner card for the Episode 1 intro/outro,
plus a 3-second still-MP4 of the same card so it can be dropped straight
into CapCut as a clip.

Inputs  : assets/branding/channel_banner.png
Outputs : data/output/capcut_package/banner_card_1080x1920.png
          data/output/capcut_package/clips/00_banner_intro.mp4 (3s)
          data/output/capcut_package/clips/99_banner_outro.mp4 (3s)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "assets" / "branding" / "channel_banner.png"
OUT  = ROOT / "data" / "output" / "capcut_package"
CLIPS = OUT / "clips"
CARD = OUT / "banner_card_1080x1920.png"

CANVAS_W, CANVAS_H = 1080, 1920
INTRO_DUR = 3.0
OUTRO_DUR = 3.0
FPS = 30


def build_card() -> Path:
    """Letterbox the banner into 1080x1920 with a soft blurred backdrop."""
    from PIL import Image, ImageFilter

    OUT.mkdir(parents=True, exist_ok=True)

    src = Image.open(SRC).convert("RGB")
    sw, sh = src.size

    # Backdrop = src cropped to 9:16 aspect, blurred. Centred on banner.
    target_aspect = CANVAS_W / CANVAS_H  # 0.5625
    src_aspect = sw / sh
    if src_aspect > target_aspect:
        # too wide - crop width
        new_w = int(sh * target_aspect)
        x0 = (sw - new_w) // 2
        bg_crop = src.crop((x0, 0, x0 + new_w, sh))
    else:
        # too tall - crop height
        new_h = int(sw / target_aspect)
        y0 = (sh - new_h) // 2
        bg_crop = src.crop((0, y0, sw, y0 + new_h))
    bg = bg_crop.resize((CANVAS_W, CANVAS_H), Image.LANCZOS).filter(
        ImageFilter.GaussianBlur(40)
    )

    # Foreground = src scaled to fit fully inside canvas with margin
    margin = 40
    max_w = CANVAS_W - 2 * margin
    max_h = CANVAS_H - 2 * margin
    scale = min(max_w / sw, max_h / sh)
    fg_w = int(sw * scale)
    fg_h = int(sh * scale)
    fg = src.resize((fg_w, fg_h), Image.LANCZOS)

    canvas = bg.copy()
    canvas.paste(fg, ((CANVAS_W - fg_w) // 2, (CANVAS_H - fg_h) // 2))
    canvas.save(CARD, "PNG", optimize=True)
    print(f"  card -> {CARD.relative_to(ROOT)}  ({CANVAS_W}x{CANVAS_H})")
    return CARD


def card_to_mp4(card_png: Path, out: Path, dur: float, *, fade_in: bool, fade_out: bool) -> None:
    """Turn the still PNG into a 3-second 30fps H.264 MP4 with optional fades."""
    fades = []
    if fade_in:
        fades.append("fade=t=in:st=0:d=0.4")
    if fade_out:
        fades.append(f"fade=t=out:st={max(0.0, dur-0.4):.2f}:d=0.4")
    vf = ",".join(["scale=1080:1920", "setsar=1", f"fps={FPS}", *fades])

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(FPS), "-t", f"{dur}", "-i", str(card_png),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an", str(out),
    ]
    subprocess.run(cmd, check=True)
    print(f"  clip -> {out.relative_to(ROOT)}  ({dur}s)")


def main() -> int:
    if not SRC.exists():
        print(f"banner not found at {SRC}")
        return 1
    CLIPS.mkdir(parents=True, exist_ok=True)
    card = build_card()
    card_to_mp4(card, CLIPS / "00_banner_intro.mp4", INTRO_DUR, fade_in=True,  fade_out=False)
    card_to_mp4(card, CLIPS / "99_banner_outro.mp4", OUTRO_DUR, fade_in=False, fade_out=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
