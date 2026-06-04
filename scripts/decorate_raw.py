#!/usr/bin/env python3
"""
decorate_raw.py
===============
Thin wrapper around the OpenAI gpt-image-1 image.edit endpoint that takes
a raw prompt file (no recipe mixing) and runs it against a single photo.

This exists so we can A/B test custom prompts without touching the more
opinionated decorate_cut.py pipeline.

Usage:
  python3 scripts/decorate_raw.py \
      --image data/assets/photos/2026/<file>.jpeg \
      --prompt-file scripts/prompts/edit_v3_en.txt \
      --quality medium

Output: data/output/decorated/raw_<stem>__<timestamp>.png  (1080x1920)

Requires OPENAI_API_KEY in .env or environment.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
OUT_DIR = REPO_ROOT / "data" / "output" / "decorated"

GEN_SIZE = "1024x1536"   # gpt-image-1 portrait
TARGET_W, TARGET_H = 1080, 1920


def to_target_9x16(src_png_bytes: bytes) -> Image.Image:
    img = Image.open(__import__("io").BytesIO(src_png_bytes)).convert("RGB")
    iw, ih = img.size
    scale = max(TARGET_W / iw, TARGET_H / ih)
    nw, nh = int(round(iw * scale)), int(round(ih * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - TARGET_W) // 2
    top  = (nh - TARGET_H) // 2
    return img.crop((left, top, left + TARGET_W, top + TARGET_H))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="Source photo path.")
    ap.add_argument("--prompt-file", required=True,
                    help="Path to plain-text prompt file (UTF-8).")
    ap.add_argument("--quality", default="medium",
                    choices=["low", "medium", "high"])
    ap.add_argument("--out", default=None,
                    help="Override output PNG path.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if load_dotenv and ENV_PATH.exists():
        load_dotenv(ENV_PATH)

    src = Path(args.image).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: image not found: {src}", file=sys.stderr)
        return 2

    pf = Path(args.prompt_file).expanduser().resolve()
    if not pf.exists():
        print(f"ERROR: prompt file not found: {pf}", file=sys.stderr)
        return 2
    prompt = pf.read_text(encoding="utf-8").strip()

    print(f"Source      : {src}")
    print(f"Prompt file : {pf}")
    print(f"Quality     : {args.quality}")
    print(f"GEN size    : {GEN_SIZE}  →  target {TARGET_W}x{TARGET_H}")
    print(f"Prompt      : ({len(prompt)} chars)")
    for line in prompt.splitlines()[:6]:
        print(f"  {line}")
    if len(prompt.splitlines()) > 6:
        print(f"  ... ({len(prompt.splitlines())-6} more lines)")
    print()

    if args.dry_run:
        print("Dry run — no API call.")
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY missing.", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install openai", file=sys.stderr)
        return 2

    # Normalize EXIF + ensure RGB JPEG that the API accepts
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    client = OpenAI(api_key=api_key)
    print("Calling gpt-image-1.edit ...")
    t0 = time.time()
    resp = client.images.edit(
        model="gpt-image-1",
        image=("input.png", buf, "image/png"),
        prompt=prompt,
        size=GEN_SIZE,
        quality=args.quality,
    )
    dt = time.time() - t0
    print(f"  done in {dt:.1f}s")

    raw = base64.b64decode(resp.data[0].b64_json)
    final = to_target_9x16(raw)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = OUT_DIR / f"raw_{src.stem}__{ts}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, "PNG")
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
