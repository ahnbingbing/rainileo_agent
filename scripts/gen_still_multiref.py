#!/usr/bin/env python3
"""Generate ONE composed still from a background + multiple character refs via
Gemini 2.5 Flash Image (nano-banana). PD 2026-06-14: build AV beats as controlled
stills (bg-locked + ref-grounded markings) so an image-sequence can be ken-burns
stitched into a drift-free video — instead of fighting Seedance i2v drift.

Multi-image input: the first image is the SCENE (room) to keep; the rest are
CHARACTER refs (pose / markings). The prompt says how to compose them.

  python3 scripts/gen_still_multiref.py \
     --bg assets/backgrounds/bg_83150ae6.jpg \
     --ref assets/character_ref/ryani_playbow.png \
     --ref assets/character_ref/leo_solo.png \
     --prompt "..." --out data/tmp/kitchen_stills/cut3.png

Cost ≈ $0.04/image. No Seedance.
"""
import argparse
import base64
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
_SSL = ssl.create_default_context(cafile=certifi.where())
MODEL = "gemini-2.5-flash-image"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def _img_part(p: Path) -> dict:
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime,
                            "data": base64.b64encode(p.read_bytes()).decode("ascii")}}


def generate(bg: Path, refs: list[Path], prompt: str, out: Path, api_key: str) -> None:
    parts = [{"text": prompt}, _img_part(bg)] + [_img_part(r) for r in refs]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                # nano-banana honors a 9:16 request via imageConfig (Shorts = vertical)
                "imageConfig": {"aspectRatio": os.getenv("STILL_ASPECT", "9:16")},
            }}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST")
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and (inline.get("mime_type") or inline.get("mimeType", "")).startswith("image/"):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(inline["data"]))
                return
    raise RuntimeError(f"no image in response: {json.dumps(payload)[:600]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bg", required=True)
    ap.add_argument("--ref", action="append", default=[], help="character ref (repeatable)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("ERROR: GOOGLE_API_KEY missing", file=sys.stderr)
        return 1
    generate(Path(a.bg), [Path(r) for r in a.ref], a.prompt, Path(a.out), key)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
