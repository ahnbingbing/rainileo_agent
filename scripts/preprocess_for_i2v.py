"""
scripts/preprocess_for_i2v.py
-----------------------------
Prepare source photos for Veo i2v (and the AI regen step that may sit between).

What it does
------------
1. Open the image and apply its EXIF orientation tag (most iPhone photos are
   stored rotated and rely on EXIF=6 to display correctly — PIL's default
   Image.open does NOT honor that, so the raw pixels are sideways).
2. Center-crop to the target aspect ratio (default 9:16 = 0.5625).
3. Resize to the target pixel size (default 720×1280 to match Episode 01's
   i2v input).
4. Save as JPEG.

Usage
-----
    # one photo
    python3 scripts/preprocess_for_i2v.py \
        --in data/assets/photos/2026/med_2026_05_06_203116_icloud_d3c5c667.jpeg \
        --out data/tmp/episode_02_input/cut1_peony_greeting.jpg

    # batch via a JSON manifest of {tag: input_path}
    python3 scripts/preprocess_for_i2v.py \
        --manifest scripts/prompts/episode_02_sources.json \
        --out-dir data/tmp/episode_02_input/

    # different target (e.g., for the AI regen which may prefer 1024×1820)
    python3 scripts/preprocess_for_i2v.py --in X --out Y --size 1024x1820

Exit codes
----------
    0   all photos processed
    1   one or more inputs missing / unreadable
    2   bad arguments
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent


def parse_size(s: str) -> tuple[int, int]:
    """`720x1280` → (720, 1280)."""
    w, h = s.lower().split("x")
    return int(w), int(h)


def preprocess(in_path: Path, out_path: Path, target_w: int, target_h: int,
               jpeg_quality: int = 92) -> None:
    """Open → EXIF-respect → center-crop to target AR → resize → save."""
    img = Image.open(in_path)

    # ImageOps.exif_transpose() reads the orientation tag and returns a NEW
    # image with the rotation/flip baked into pixels, then strips the tag so
    # the downstream consumer (Veo, the AI regen, ffmpeg) won't double-apply.
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    target_ar = target_w / target_h          # e.g., 720/1280 = 0.5625
    src_w, src_h = img.size
    src_ar = src_w / src_h

    if src_ar > target_ar:
        # source is too WIDE for target aspect — crop horizontally (center)
        new_w = int(round(src_h * target_ar))
        x0 = (src_w - new_w) // 2
        img = img.crop((x0, 0, x0 + new_w, src_h))
    elif src_ar < target_ar:
        # source is too TALL — crop vertically (center)
        new_h = int(round(src_w / target_ar))
        y0 = (src_h - new_h) // 2
        img = img.crop((0, y0, src_w, y0 + new_h))
    # else: already exact AR, no crop

    img = img.resize((target_w, target_h), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=jpeg_quality, optimize=True)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", default=None,
                   help="single input image path")
    p.add_argument("--out", dest="out_path", default=None,
                   help="single output image path (use with --in)")
    p.add_argument("--manifest", default=None,
                   help='JSON file: {"tag1": "input/path1.jpg", ...} — batch mode')
    p.add_argument("--out-dir", default=None,
                   help="batch output dir (used with --manifest)")
    p.add_argument("--size", default="720x1280",
                   help="target WxH (default 720x1280 — match Episode 01)")
    p.add_argument("--quality", type=int, default=92,
                   help="JPEG quality 1-100 (default 92)")
    args = p.parse_args()

    try:
        target_w, target_h = parse_size(args.size)
    except (ValueError, AttributeError):
        print(f"ERROR: bad --size '{args.size}', expected WxH", file=sys.stderr)
        return 2

    # Determine the work list
    jobs: list[tuple[str, Path, Path]] = []
    if args.manifest:
        if not args.out_dir:
            print("ERROR: --manifest requires --out-dir", file=sys.stderr)
            return 2
        man_path = Path(args.manifest)
        if not man_path.exists():
            print(f"ERROR: manifest {man_path} not found", file=sys.stderr)
            return 2
        out_dir = Path(args.out_dir)
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        for tag, src in manifest.items():
            if tag.startswith("_"):
                continue  # metadata keys
            src_path = (ROOT / src).resolve() if not Path(src).is_absolute() \
                       else Path(src)
            dst_path = out_dir / f"{tag}.jpg"
            jobs.append((tag, src_path, dst_path))
    elif args.in_path and args.out_path:
        jobs.append(("single", Path(args.in_path), Path(args.out_path)))
    else:
        print("ERROR: provide either --in/--out OR --manifest/--out-dir",
              file=sys.stderr)
        return 2

    failures = 0
    for tag, src, dst in jobs:
        if not src.exists():
            print(f"  ! {tag}: {_rel(src)} not found", file=sys.stderr)
            failures += 1
            continue
        try:
            preprocess(src, dst, target_w, target_h, args.quality)
        except Exception as e:
            print(f"  ! {tag}: {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1
            continue
        size_kb = dst.stat().st_size / 1024
        print(f"  ok {tag:25s} → {_rel(dst)}  ({target_w}×{target_h}, {size_kb:.0f} KB)")

    # PD 2026-06-12: a PARTIAL failure (some photos missing/pruned) must NOT fail the
    # whole step — the caller already dropped truly-unavailable cuts; the rest render.
    # Fail (rc=1) ONLY when EVERY job failed (nothing usable produced).
    done = len(jobs) - failures
    if failures:
        print(f"  preprocess: {done}/{len(jobs)} ok, {failures} skipped", file=sys.stderr)
    return 0 if done > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
