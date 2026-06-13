"""scripts/photo_sequence.py — turn a time-clustered PHOTO BURST into a flowing
9:16 "video" cut (PD 2026-06-13 idea).

WHY: ~318 same-session photo bursts (1,719 photos, ≤2min clusters) sit unused while
the 490 video clips get over-shot. Sequencing same-session stills — each with a gentle
ken-burns push + crossfades — reads as a memory-lane video, is a HUGE fresh source, and
(being pet bursts) avoids the bystander-face HARD RULE that cafe video keeps tripping.

MVP: per-photo ken-burns (zoompan) → crossfade concat. Photos are pre-normalized to a
filled 9:16 frame via PIL (clean EXIF/orientation handling; zoompan alone jitters on
raw mixed-orientation input).

Usage:
  python3 scripts/photo_sequence.py --photos /tmp/seq.json --out out.mp4 \
      --per 1.6 --xfade 0.45 --zoom 0.12 [--max 12]
  (--photos is a JSON array of absolute image paths, in order)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

W, H = 720, 1280
FPS = 30


def _normalize(src: Path, dst: Path, up: float = 1.18) -> bool:
    """EXIF-transpose, cover-crop to 9:16, save at slightly-larger-than-output size so
    the ken-burns zoom has headroom without upscaling blur."""
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            tw, th = int(W * up), int(H * up)
            im = ImageOps.fit(im, (tw, th), method=Image.LANCZOS, centering=(0.5, 0.42))
            im.save(dst, "JPEG", quality=92)
        return True
    except Exception as e:
        print(f"  normalize failed {src.name}: {e}")
        return False


def _kenburns_clip(img: Path, out: Path, dur: float, zoom: float, idx: int) -> bool:
    """One still → a dur-second 9:16 clip with a slow zoom (alternating in/out + a
    gentle drift so consecutive cuts don't feel identical)."""
    frames = max(2, int(dur * FPS))
    # alternate zoom-in / zoom-out by index for variety
    if idx % 2 == 0:
        z = f"min(zoom+{zoom/ (dur*FPS):.6f},{1+zoom:.3f})"
    else:
        z = f"max({1+zoom:.3f}-(on/{frames})*{zoom:.3f},1.0)"
    # slight vertical drift
    y = "ih/2-(ih/zoom/2)+sin(on/" + str(frames) + "*3.14159)*8"
    vf = (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='{y}':d={frames}:"
          f"s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-loop", "1",
           "-i", str(img), "-t", f"{dur:.3f}", "-r", str(FPS),
           "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        print(f"  kenburns failed [{idx}]: {r.stderr[-300:]}")
        return False
    return True


def _xfade_concat(clips: list[Path], out: Path, xfade: float, per: float) -> bool:
    """Chain clips with xfade crossfades."""
    if len(clips) == 1:
        clips[0].replace(out)
        return True
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    # build filter: progressively xfade
    fc = []
    prev = "[0:v]"
    offset = 0.0
    for i in range(1, len(clips)):
        offset += per - xfade
        lbl = f"[x{i}]"
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={xfade:.3f}:"
                  f"offset={offset:.3f}{lbl}")
        prev = lbl
    filt = ";".join(fc)
    cmd = ["ffmpeg", "-y", "-nostats", "-loglevel", "error", *inputs,
           "-filter_complex", filt, "-map", prev,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        print(f"  xfade concat failed: {r.stderr[-400:]}")
        return False
    return True


def render(photos: list[str], out: Path, per: float = 1.6, xfade: float = 0.45,
           zoom: float = 0.12, max_n: int | None = None) -> bool:
    paths = [Path(p) for p in photos if Path(p).exists()]
    if max_n:
        paths = paths[:max_n]
    if not paths:
        print("no photos")
        return False
    print(f"rendering {len(paths)} photos → {out.name} (per={per}s xfade={xfade}s)")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        clips = []
        for i, p in enumerate(paths):
            norm = tdp / f"n{i}.jpg"
            if not _normalize(p, norm):
                continue
            clip = tdp / f"c{i}.mp4"
            if _kenburns_clip(norm, clip, per, zoom, i):
                clips.append(clip)
        if not clips:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        return _xfade_concat(clips, out, xfade, per)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", required=True, help="JSON array of image paths")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per", type=float, default=1.6)
    ap.add_argument("--xfade", type=float, default=0.45)
    ap.add_argument("--zoom", type=float, default=0.12)
    ap.add_argument("--max", type=int, default=None)
    a = ap.parse_args()
    photos = json.loads(Path(a.photos).read_text())
    ok = render(photos, Path(a.out), a.per, a.xfade, a.zoom, a.max)
    print("OK" if ok else "FAILED", a.out)


if __name__ == "__main__":
    main()
