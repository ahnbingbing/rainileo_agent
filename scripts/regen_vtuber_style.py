"""
scripts/regen_vtuber_style.py
-----------------------------
AI image regen (Gemini 2.5 Flash Image / nano-banana) for vtuber-style photo
transformation. Replaces sticker overlay with full AI synthesis — the photo
itself gets composited with neon/lotus/sparkle decorations baked into the
pixels (matching the lucy_bday reference vibe).

Pipeline
--------
    data/tmp/episode_NN_input/<tag>.jpg          (from preprocess_for_i2v.py)
    + scripts/prompts/episode_NN_regen_prompts.json
    →  data/tmp/episode_NN_regen/<tag>.png

The output PNG is what gets fed to Veo i2v.

Cost
----
Gemini 2.5 Flash Image is ~$0.04/image as of pricing snapshot. 4 cuts ≈ $0.16
per episode. Cheap enough to iterate multiple seeds.

Why nano-banana not Imagen 3
----------------------------
Imagen 3 generate-002 doesn't support image-to-image directly (text-to-image
only). Gemini 2.5 Flash Image natively accepts an image input + text prompt,
which is what we need for "take this photo, restyle it like X" tasks. Same
GOOGLE_API_KEY works.

Usage
-----
    # one cut (test/iterate)
    python3 scripts/regen_vtuber_style.py --cut cut1_peony_greeting

    # all cuts in the manifest
    python3 scripts/regen_vtuber_style.py

    # custom input/output/prompt dirs (for episode 03+ reuse)
    python3 scripts/regen_vtuber_style.py \
        --in-dir data/tmp/episode_03_input/ \
        --out-dir data/tmp/episode_03_regen/ \
        --prompts scripts/prompts/episode_03_regen_prompts.json

    # different seed (Gemini doesn't honor seed deterministically, but you
    # can re-run and pick the best of N)
    python3 scripts/regen_vtuber_style.py --cut cut1_peony_greeting --n 3

Env
---
    GOOGLE_API_KEY  — same key as motion_b_vlm.py / animate_all_cuts.sh

Exit codes
----------
    0   all requested cuts succeeded
    1   one or more cuts failed
    2   bad setup (api key missing, files missing)

Dependencies
------------
    pip install pillow  (already used elsewhere)
    Python stdlib urllib + base64 (no extra deps)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import ssl
import urllib.request
import urllib.error
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

try:
    import certifi
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IN_DIR = ROOT / "data" / "tmp" / "episode_02_input"
DEFAULT_OUT_DIR = ROOT / "data" / "tmp" / "episode_02_regen"
DEFAULT_PROMPTS = ROOT / "scripts" / "prompts" / "episode_02_regen_prompts.json"

# Gemini 2.5 Flash Image — the "nano-banana" model family. Returns image
# inline in `parts[].inline_data.data` (base64).
#
# Available image-capable generateContent models (as of 2026-05):
#   gemini-2.5-flash-image           ← default. Flash tier, cheap+fast.
#   gemini-3.1-flash-image-preview   ← newer Flash, slightly better quality.
#   gemini-3-pro-image-preview       ← Pro tier, best quality, slower+pricier.
#   nano-banana-pro-preview          ← alias of gemini-3-pro-image-preview.
#
# Imagen 4 (imagen-4.0-*) is also available but uses `predict` (not
# generateContent) and is text-to-image only — incompatible with this script.
MODEL = "gemini-2.5-flash-image"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def build_prompt(prompts: dict, tag: str) -> str:
    """Stitch base style + preservation rules + per-cut detail into one prompt."""
    base = prompts.get("_base_style", "").strip()
    preserve = prompts.get("_preserve_subjects", "").strip()
    cut = prompts.get(tag, "").strip()
    parts = [p for p in (base, preserve, cut) if p]
    return "\n\n".join(parts)


def regen_one(img_path: Path, prompt: str, api_key: str) -> bytes:
    """Call Gemini 2.5 Flash Image with the photo + prompt. Returns PNG bytes.

    The model returns the generated image as inline_data with base64-encoded
    bytes. We look for the first part whose mime_type starts with 'image/'.
    """
    image_b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")

    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
            ],
        }],
        # responseModalities is the explicit way to tell Gemini to emit images
        # alongside (or instead of) text. Without it the model may default to
        # text-only on some endpoints.
        "generationConfig": {
            "responseModalities": ["IMAGE"],
        },
    }

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:500]}") from e

    # Walk the response for the first image part.
    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inline_data") or part.get("inlineData")
            if not inline:
                continue
            mime = inline.get("mime_type") or inline.get("mimeType", "")
            if mime.startswith("image/"):
                return base64.b64decode(inline["data"])

    raise RuntimeError(
        f"no image part in response. Raw payload: {json.dumps(payload)[:800]}"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cut", default=None,
                   help="process a single tag (default: all cuts in prompts)")
    p.add_argument("--in-dir", default=str(DEFAULT_IN_DIR),
                   help=f"preprocessed input dir (default: {_rel(DEFAULT_IN_DIR)})")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                   help=f"regen output dir (default: {_rel(DEFAULT_OUT_DIR)})")
    p.add_argument("--prompts", default=str(DEFAULT_PROMPTS),
                   help=f"prompt manifest JSON (default: {_rel(DEFAULT_PROMPTS)})")
    p.add_argument("--n", type=int, default=1,
                   help="how many variants per cut (saved as <tag>_v1.png ...)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the assembled prompt for each cut and exit")
    args = p.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not args.dry_run and not api_key:
        print("ERROR: GOOGLE_API_KEY not set in env", file=sys.stderr)
        return 2

    prompts_path = Path(args.prompts)
    if not prompts_path.exists():
        print(f"ERROR: prompts manifest {prompts_path} not found",
              file=sys.stderr)
        return 2
    prompts = json.loads(prompts_path.read_text(encoding="utf-8"))

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cut:
        tags = [args.cut]
    else:
        tags = [k for k in prompts if not k.startswith("_")]

    failures = 0
    for tag in tags:
        if tag not in prompts:
            print(f"  ! {tag} missing from prompts manifest", file=sys.stderr)
            failures += 1
            continue

        src = in_dir / f"{tag}.jpg"
        if not src.exists():
            print(f"  ! {tag}: input {_rel(src)} not found "
                  f"(run preprocess_for_i2v.py first)", file=sys.stderr)
            failures += 1
            continue

        prompt = build_prompt(prompts, tag)
        print(f"==> {tag}")
        print(f"    src    = {_rel(src)}")
        print(f"    prompt = {prompt[:140]}{'…' if len(prompt) > 140 else ''}")

        if args.dry_run:
            print(f"    [dry-run] full prompt:")
            for line in prompt.split("\n"):
                print(f"      {line}")
            continue

        for i in range(1, args.n + 1):
            suffix = f"_v{i}" if args.n > 1 else ""
            out_path = out_dir / f"{tag}{suffix}.png"
            try:
                png_bytes = regen_one(src, prompt, api_key)
            except Exception as e:
                print(f"    ! variant {i}: {type(e).__name__}: {e}",
                      file=sys.stderr)
                failures += 1
                continue
            out_path.write_bytes(png_bytes)
            size_kb = out_path.stat().st_size / 1024
            print(f"    ok v{i} ({size_kb:.0f} KB) → {_rel(out_path)}")

    print()
    if failures:
        print(f"done — {failures} failure(s)")
        return 1
    print("done — all cuts regen'd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
