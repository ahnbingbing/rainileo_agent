"""
scripts/extract_clips_ep04.py
-----------------------------
EP04 일상 pipeline — extract video clip windows, scale/pad to 1080×1920,
and burn TOP-positioned narrator captions in one ffmpeg pass per cut.

Why this script (vs the EP01-03 pipeline)
-----------------------------------------
EP01-03 went photo → AI regen → Veo i2v → burn_captions.py. EP04 starts
from REAL video footage (data/assets/clips/<year>/*.mov), so:
  - No preprocess_for_i2v.py (no photo crop)
  - No regen_vtuber_style.py (실사 유지)
  - No animate_hero_veo3.py (이미 video)
  - No burn_captions.py — captions are at TOP position with marker style,
    different enough that it's simpler to merge into the extract step.

Single ffmpeg call per cut:
    -ss <trim_start> -t <trim_dur> -i <source>
    -vf "scale+pad to 1080x1920, drawtext(KO top), drawtext(EN below)"
    -an  → output to data/output/animated_captioned/<tag>.mp4

The output drops into the same dir as EP01-03 captioned cuts, so
assemble_episode.py picks them up via --captions episode_04_captions.json
without any other change.

Caption style (different from EP01-03 bottom-positioned KO/EN):
  - Position: top center (한국 예능 자막 정통)
  - Font: Nanum Pen Script (handwritten/marker feel) with Pretendard
    ExtraBold fallback if Nanum not installed.
  - KO larger (72px), EN smaller (50px) below.
  - White text + thick black outline (marker on photo feel).
  - 괄호 narrator 톤 — the parentheses are part of the text content.

Install Nanum Pen Script font:
    brew install --cask font-nanum-pen-script

Usage
-----
    # all 4 cuts
    python3 scripts/extract_clips_ep04.py

    # single cut (re-run / debug)
    python3 scripts/extract_clips_ep04.py --cut cut3_ryani_sleeping

    # dry-run — print the ffmpeg command
    python3 scripts/extract_clips_ep04.py --dry-run

    # different episode (the script is generalized)
    python3 scripts/extract_clips_ep04.py \
        --sources scripts/prompts/episode_05_sources.json \
        --captions scripts/prompts/episode_05_captions.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = ROOT / "scripts" / "prompts" / "episode_04_sources.json"
DEFAULT_CAPTIONS = ROOT / "scripts" / "prompts" / "episode_04_captions.json"
OUT_DIR_DEFAULT = ROOT / "data" / "output" / "animated_captioned"
TMP_DIR = ROOT / "data" / "tmp" / "ep04_captions"

# Font hunt order — Nanum Pen Script (handwritten marker feel) preferred,
# Pretendard ExtraBold acceptable fallback (bold modern, ~80% of marker tone).
FONT_CANDIDATES = [
    Path.home() / "Library" / "Fonts" / "NanumPenScript-Regular.ttf",
    Path.home() / "Library" / "Fonts" / "NanumPen.ttf",
    Path.home() / "Library" / "Fonts" / "Nanum Pen.ttf",
    Path.home() / "Library" / "Fonts" / "NanumPenScript.ttf",
    Path.home() / "Library" / "Fonts" / "Pretendard-ExtraBold.otf",
    Path.home() / "Library" / "Fonts" / "Pretendard-Black.otf",
]

# Output spec
W = 1080
H = 1920
FPS = 30

# Caption style — marker on photo (top position)
KO_SIZE = 48
EN_SIZE = 50
KO_Y = 280           # px from top — safe zone below notch/status bar
EN_Y = 380           # px from top, leaves ~30px gap below KO
KO_BORDER = 6        # px black outline — thick for marker on photo
EN_BORDER = 4
SHADOW_X = 3
SHADOW_Y = 3


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def find_font() -> Path:
    for f in FONT_CANDIDATES:
        if f.exists():
            return f
    print("WARNING: no preferred font found, falling back to system default",
          file=sys.stderr)
    print("  Install Nanum Pen Script: brew install --cask font-nanum-pen-script",
          file=sys.stderr)
    # last-ditch fallback — DejaVu (sandbox only) or whatever fontconfig picks
    return Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def build_vf(font: Path, scenes: list[dict], tag: str, tmp_dir: Path,
             pan: str | None = None, trim_dur: float = 4.0) -> str:
    """Filter chain: scale+crop source to 1080×1920 (no black bars),
    then one timed drawtext per caption scene (KO + EN pair per scene).

    Scaling: `force_original_aspect_ratio=increase` makes the LARGER dimension
    overflow target, then `crop` trims the overflow. Result: source content
    "covers" the 9:16 frame — landscape gets sides cropped, no letterbox.
    Subjects in the center stay in frame. (Compare to scale+pad which keeps
    everything visible but adds black bars.)

    pan: "left_to_right" or "right_to_left" — animates the crop x position
    over the duration so the camera pans across a wide source.

    Each scene gets its own drawtext entry with `enable='between(t,s,e)'` so
    multiple scenes can be defined per cut and the captions update as time
    progresses — matches the "재잘재잘 narrator" tone from the reference.
    """
    if pan == "left_to_right":
        # After scale, the frame is taller than H so height matches,
        # but wider than W. crop x slides from 0 → (in_w - W) over trim_dur.
        crop_expr = f"crop={W}:{H}:x='(in_w-{W})*t/{trim_dur}':y=0"
    elif pan == "right_to_left":
        crop_expr = f"crop={W}:{H}:x='(in_w-{W})*(1-t/{trim_dur})':y=0"
    else:
        crop_expr = f"crop={W}:{H}"
    base = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"{crop_expr},"
        f"setsar=1,fps={FPS}"
    )
    chains = [base]
    for i, scene in enumerate(scenes):
        ko_file = tmp_dir / f"{tag}_s{i}_ko.txt"
        en_file = tmp_dir / f"{tag}_s{i}_en.txt"
        ko_file.write_text(scene["ko"], encoding="utf-8")
        en_file.write_text(scene["en"], encoding="utf-8")
        start = float(scene["start"])
        end = float(scene["end"])
        # Short per-scene fade-in (0.15s) makes the swap feel natural, not jumpy.
        # No fade-out — clean cut at scene's end keeps narration brisk.
        fade_in = 0.15
        alpha = (
            f"if(lt(t,{start}),0,"
            f"if(lt(t,{start + fade_in}),(t-{start})/{fade_in},1))"
        )
        ko_draw = (
            f"drawtext=fontfile='{font}':textfile='{ko_file}':"
            f"fontsize={KO_SIZE}:fontcolor=white:"
            f"borderw={KO_BORDER}:bordercolor=black:"
            f"shadowcolor=black@0.6:shadowx={SHADOW_X}:shadowy={SHADOW_Y}:"
            f"x=(w-text_w)/2:y={KO_Y}:"
            f"enable='between(t\\,{start}\\,{end})':"
            f"alpha='{alpha}'"
        )
        en_draw = (
            f"drawtext=fontfile='{font}':textfile='{en_file}':"
            f"fontsize={EN_SIZE}:fontcolor=white:"
            f"borderw={EN_BORDER}:bordercolor=black:"
            f"shadowcolor=black@0.5:shadowx=2:shadowy=2:"
            f"x=(w-text_w)/2:y={EN_Y}:"
            f"enable='between(t\\,{start}\\,{end})':"
            f"alpha='{alpha}'"
        )
        chains.extend([ko_draw, en_draw])
    return ",".join(chains)


def normalize_scenes(caption_entry: dict, trim_dur: float) -> list[dict]:
    """Accept either the new {"scenes": [...]} shape or the legacy flat
    {"ko": ..., "en": ...}. Returns a list of scene dicts."""
    if "scenes" in caption_entry:
        return caption_entry["scenes"]
    # legacy single-line — render full clip
    return [{
        "start": 0.3,
        "end": trim_dur,
        "ko": caption_entry.get("ko", ""),
        "en": caption_entry.get("en", ""),
    }]


def extract_one(src: Path, trim_start: float, trim_dur: float,
                scenes: list[dict], tag: str, font: Path,
                out: Path, pan: str | None = None) -> list[str]:
    """Build the full ffmpeg command for one cut. Returns argv list.

    Caller runs subprocess.run. `-ss` BEFORE `-i` does fast seek (keyframe-
    accurate enough for our 0.5s-granularity trims and much faster than
    sample-accurate output seek).
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    vf = build_vf(font, scenes, tag, TMP_DIR, pan=pan, trim_dur=trim_dur)
    return [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-ss", f"{trim_start:.3f}",
        "-t", f"{trim_dur:.3f}",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an",                       # strip source audio — BGM in assemble
        "-movflags", "+faststart",
        str(out),
    ]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default=str(DEFAULT_SOURCES),
                   help=f"sources manifest (default: {_rel(DEFAULT_SOURCES)})")
    p.add_argument("--captions", default=str(DEFAULT_CAPTIONS),
                   help=f"captions manifest (default: {_rel(DEFAULT_CAPTIONS)})")
    p.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT),
                   help=f"output dir (default: {_rel(OUT_DIR_DEFAULT)})")
    p.add_argument("--cut", default=None,
                   help="process a single cut tag (default: all)")
    p.add_argument("--dry-run", action="store_true",
                   help="print ffmpeg command without running")
    args = p.parse_args()

    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found in PATH", file=sys.stderr)
        return 2

    sources_path = Path(args.sources)
    captions_path = Path(args.captions)
    for p_ in (sources_path, captions_path):
        if not p_.exists():
            print(f"ERROR: manifest {p_} not found", file=sys.stderr)
            return 2

    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    captions = json.loads(captions_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    font = find_font()
    print(f"font: {font}")
    print()

    # Iterate using captions manifest's cut order (matches assemble_episode.py)
    tags = [k for k, v in captions.items()
            if not k.startswith("_") and isinstance(v, dict)]
    if args.cut:
        if args.cut not in tags:
            print(f"ERROR: {args.cut} not in captions manifest", file=sys.stderr)
            return 2
        tags = [args.cut]

    failures = 0
    for tag in tags:
        if tag not in sources:
            print(f"  ! {tag} missing from sources manifest", file=sys.stderr)
            failures += 1
            continue
        src_entry = sources[tag]
        src = Path(src_entry["source"])
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            print(f"  ! {tag}: source {_rel(src)} not found", file=sys.stderr)
            failures += 1
            continue

        trim_start = float(src_entry.get("trim_start", 0.0))
        trim_dur = float(src_entry.get("trim_dur", 4.0))
        pan = src_entry.get("pan")
        scenes = normalize_scenes(captions[tag], trim_dur)

        out = out_dir / f"{tag}.mp4"
        cmd = extract_one(src, trim_start, trim_dur, scenes, tag, font, out, pan=pan)

        print(f"==> {tag}")
        print(f"    src   = {_rel(src)}")
        print(f"    trim  = {trim_start:.1f}s + {trim_dur:.1f}s")
        print(f"    scenes ({len(scenes)}):")
        for s in scenes:
            print(f"      {s['start']:.1f}-{s['end']:.1f}s  ko={s['ko']!r}")
        print(f"    out   = {_rel(out)}")

        if args.dry_run:
            print("    [dry-run] ffmpeg cmd:")
            print("      " + " ".join(repr(a) if " " in a else a for a in cmd))
            print()
            continue

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"    ! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
            failures += 1
            continue

        size_mb = out.stat().st_size / 1e6
        print(f"    ok ({size_mb:.2f} MB)")
        print()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
