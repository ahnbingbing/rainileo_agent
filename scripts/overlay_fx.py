"""overlay_fx.py — composite a themed graphic EFFECT onto a rendered AV cut, the right
way, in ONE step (PD 2026-06-24, "이게 v12까지 갈 일이야? 근본원인 + 전체 업데이트").

Why this exists
---------------
Seedance renders pets+rooms but NOT graphic flourishes (jackpot signs, fireworks, neon,
scoreboards, speech bubbles). When a concept's THEME needs such a flourish, we composite it
AFTER render. The churu "JACKPOT" effect took 12 manual iterations because the spec and the
compositing pitfalls were rediscovered one PD note at a time. This module bakes BOTH the
SPEC and the proven technical recipe so it's reproducible:

SPEC (decide up front, don't iterate):
  - the graphic is generated WITH MARGINS — the whole element (e.g. both rounded ends of a
    marquee banner) sits fully inside with empty room around it, so scaling never clips it.
  - it is placed at the TOP by default so it never covers the pet's FACE (center).
  - it is sized to a fraction of frame width (not stretched — aspect preserved).
  - it ANIMATES (Seedance burst) — a static stamp reads as dead.

RECIPE (the technical traps, solved once):
  - generate on PURE BLACK (not transparent — gpt transparency is unreliable and Seedance
    needs an opaque frame anyway).
  - Seedance i2v animates the burst; the prompt MUST say "black background stays pure black".
  - composite by CRUSHING blacks first (curves) THEN lumakey — this removes the faint
    rectangular boundary box that a raw lumakey leaves when Seedance's black isn't perfectly
    pure. NEVER screen-blend a bright/colored graphic (it washes the whole frame pink).
  - keep the banner SOLID: crush only true black (0.10/0), low lumakey threshold (~0.085),
    so the colored body survives while the bg keys out cleanly.

Usage
-----
  # full auto: generate → animate → composite
  python -m scripts.overlay_fx --base cut4.mp4 --out cut4_fx.mp4 \
      --theme "classic Las Vegas JACKPOT marquee sign" --burst-at 2.45

  # reuse an already-animated overlay (skip gen+seedance)
  python -m scripts.overlay_fx --base cut4.mp4 --out cut4_fx.mp4 \
      --anim jackpot_anim.mp4 --burst-at 2.45 --position top

Cost: ~$0.04 image + ~1 Seedance call when generating. Reuse --anim to iterate free.
"""
from __future__ import annotations
import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Position presets → overlay y-shift (negative = banner rides at the TOP, pet face clear).
# Tuned on the churu fix: a 720x1280 overlay whose element sits ~mid-upper needs ~-360 to
# pull the element to the frame top.
_POS_YSHIFT = {"top": -360, "upper": -200, "center": 0}


def _gen_overlay_image(theme: str, out_png: Path) -> Path:
    """Generate the theme graphic on PURE BLACK with the whole element + margins."""
    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    prompt = (
        f"{theme}, on a solid PURE BLACK background. ★The ENTIRE graphic element is fully "
        f"visible and centered in the UPPER portion with clear empty MARGIN on every side — "
        f"it must NOT touch or run off any edge. Around/behind it, gold ribbons, streamers, "
        f"confetti and sparks burst outward like fireworks. The LOWER-CENTER area stays empty "
        f"black. Vibrant glossy celebratory style. No watermark, no text other than the graphic."
    )
    r = OpenAI().images.generate(
        model=os.getenv("OPENAI_IMAGE_MODEL_TRANSP", "gpt-image-1"),
        prompt=prompt, size="1024x1536", quality="high", n=1)
    out_png.write_bytes(base64.b64decode(r.data[0].b64_json))
    print(f"  overlay image → {out_png} ({out_png.stat().st_size//1024} KB)")
    return out_png


def _animate_overlay(img: Path, out_mp4: Path, seconds: int = 5) -> Path:
    """Seedance-animate the burst (ribbons/confetti fly outward); black stays black."""
    prompt = (
        "The central sign/graphic stays in place and glows; the gold ribbons, streamers, "
        "confetti and sparks around it BURST OUTWARD like party poppers / fireworks (팡!), "
        "flying in all directions and fluttering, sparkles twinkling. The pure BLACK "
        "background stays solid pure black everywhere. High-energy celebratory motion."
    )
    cmd = [sys.executable, "scripts/animate_seedance_i2v.py", "--mode", "i2v",
           "--image", str(img), "--seconds", str(seconds), "--ratio", "9:16",
           "--prompt", prompt, "--output", str(out_mp4)]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)
    print(f"  overlay anim → {out_mp4}")
    return out_mp4


def composite(base: Path, anim: Path, out: Path, *, burst_at: float = 2.45,
              position: str = "top", width_frac: float = 1.0, hold: float = 2.6) -> Path:
    """Composite the animated overlay onto `base` from t=burst_at.

    The proven recipe: crush blacks (curves) → clean lumakey (no boundary box) → overlay
    shifted so the element rides at `position` (top keeps the pet's face clear). Element is
    scaled by aspect (never stretched). `hold` = seconds of the burst shown (trimmed)."""
    import json
    # match the base cut's native size (AV cuts are 720x1280; never assume 1080x1920)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(base)],
        capture_output=True, text=True).stdout
    st = json.loads(probe)["streams"][0]
    W, H = int(st["width"]), int(st["height"])
    ow = int(W * width_frac)
    yshift = _POS_YSHIFT.get(position, _POS_YSHIFT["top"])
    # scale to ow wide (aspect-preserved), pre-black-pad to burst_at, crush blacks, lumakey
    fc = (
        f"[1:v]scale={ow}:-1,setsar=1,trim=0:{hold},setpts=PTS-STARTPTS,"
        f"curves=all='0/0 0.10/0 1/1',"
        f"tpad=start_duration={burst_at}:color=black,"
        f"lumakey=threshold=0.085:tolerance=0.07:softness=0.08[fx];"
        f"[0:v][fx]overlay=x=(W-w)/2:y={yshift}:enable='gte(t,{burst_at})'[v]"
    )
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(base), "-i", str(anim),
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19", "-c:a", "copy", str(out), "-y"]
    subprocess.run(cmd, check=True)
    print(f"  composited → {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Composite a themed graphic FX onto an AV cut")
    ap.add_argument("--base", required=True, help="rendered cut mp4 to composite onto")
    ap.add_argument("--out", required=True)
    ap.add_argument("--theme", help="graphic to generate (e.g. 'Las Vegas JACKPOT marquee sign')")
    ap.add_argument("--anim", help="reuse an already-animated overlay mp4 (skips gen+seedance)")
    ap.add_argument("--burst-at", type=float, default=2.45, help="seconds into the cut when it pops")
    ap.add_argument("--position", choices=list(_POS_YSHIFT), default="top",
                    help="top (default, keeps face clear) | upper | center")
    ap.add_argument("--width-frac", type=float, default=1.0, help="overlay width as frac of frame")
    ap.add_argument("--workdir", default="/tmp/overlay_fx")
    args = ap.parse_args()
    wd = Path(args.workdir); wd.mkdir(parents=True, exist_ok=True)
    anim = Path(args.anim) if args.anim else None
    if anim is None:
        if not args.theme:
            print("need --theme (to generate) or --anim (to reuse)"); return 2
        img = _gen_overlay_image(args.theme, wd / "fx_img.png")
        anim = _animate_overlay(img, wd / "fx_anim.mp4")
    composite(Path(args.base), anim, Path(args.out),
              burst_at=args.burst_at, position=args.position, width_frac=args.width_frac)
    print("OVERLAY_FX_OUT:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
