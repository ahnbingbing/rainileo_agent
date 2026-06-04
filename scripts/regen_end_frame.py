"""
scripts/regen_end_frame.py
--------------------------
Generate an END frame for Veo 3.1's first+last frame interpolation mode.

Why this exists
---------------
Veo 3.1 lite (default) and even standard tier produce conservative motion on
heavily decorated/illustrated regen images — the model can't tell subjects
from props, so it plays safe and animates very little. Output looks like a
beautiful but static frame.

Veo 3.1 supports `instances[].lastFrame`: if you pass both a start image and
an end image, the model interpolates between them over the requested duration.
That FORCES motion of whatever the difference is between the two frames.

For cut3_dance_party, this means: generate an end frame where the pets are in
a peak dance pose (paws up, mid-bounce, mouth open) while keeping the same
background. Veo will animate the transition from "lying / sitting" → "mid-
dance climax" → produces the dynamic dance we want.

How it works
------------
1. Reads the existing (already-vtuber-styled) cut3 PNG from
   data/tmp/episode_02_regen/<tag>.png
2. Sends it to Gemini 2.5 Flash Image with a "modify only the pose" prompt
   that preserves all decorations and character identity.
3. Writes the output to data/tmp/episode_02_regen/<tag>_end.png

The end-frame regen is the same model as the start regen, so visual style
stays consistent — only the pose changes (ideally; Gemini is stochastic,
sometimes it re-paints too much. Re-run with --n 3 to pick the best variant).

Usage
-----
    # generate cut3 end frame (default behavior)
    python3 scripts/regen_end_frame.py

    # different cut (e.g. cut4 if we ever want interpolation there too)
    python3 scripts/regen_end_frame.py --cut cut4_cuddle_peace

    # multiple variants, pick the best (each variant is +~$0.04)
    python3 scripts/regen_end_frame.py --n 3
    open data/tmp/episode_02_regen/cut3_dance_party_end_v*.png

    # dry-run — print the full prompt without API call
    python3 scripts/regen_end_frame.py --dry-run

Env
---
    GOOGLE_API_KEY  — same key as motion_b_vlm.py / regen_vtuber_style.py
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
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
REGEN_DIR = ROOT / "data" / "tmp" / "episode_02_regen"

MODEL = "gemini-2.5-flash-image"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# Pose-change prompts per cut. Each prompt MUST:
#   - explicitly preserve background, decorations, lighting, framing
#   - lock down character identity (Ryani's no-tail Frenchie + white markings)
#   - describe ONLY the pose change clearly so Gemini knows what to vary.
END_FRAME_PROMPTS: dict[str, str] = {
    "cut3_dance_party": (
        "Modify ONLY the pose of the two pets in this image. Keep everything "
        "else exactly the same — same background, same camera angle, same "
        "framing, same confetti, same paper lanterns, same neon glow, same "
        "party hats, same music notes, same lighting, same color palette.\n\n"
        "Character identity (CRITICAL — do not alter these traits):\n"
        " - Orange tabby cat (Leo) — preserve all tabby stripes, amber eyes, "
        "pink nose, white whiskers, exact body proportions.\n"
        " - Small French bulldog (Ryani) — brachycephalic flat-faced breed, "
        "NO TAIL, mostly black BUT WITH DISTINCTIVE WHITE MARKINGS that you "
        "MUST PRESERVE: white blaze on chin/muzzle, white chest patch, white "
        "toes/paws. Do NOT paint over the white markings.\n\n"
        "New pose — both pets at the peak of a joyful dance:\n"
        " - Orange tabby cat (Leo) is now sitting up tall on its haunches "
        "with both front paws raised high in the air, head tilted back in "
        "happy expression, mouth slightly open.\n"
        " - French bulldog (Ryani) is now standing on her hind legs, body "
        "upright, mouth open in joyful expression, ears bouncing upward.\n"
        " - Both pets look full of energy, mid-bounce, celebrating together.\n\n"
        "9:16 vertical composition. Same vtuber kawaii art style as input."
    ),
    "cut4_cuddle_peace": (
        "Modify ONLY the pose of the two pets in this image. Keep everything "
        "else exactly the same — same background, same camera angle, same "
        "paper lanterns, same lotus petals, same lighting.\n\n"
        "Character identity (CRITICAL):\n"
        " - Orange tabby cat (Leo) — preserve all tabby markings, body shape.\n"
        " - French bulldog (Ryani) — NO TAIL, preserve white face blaze, "
        "white chest patch, white paws.\n\n"
        "New pose — small natural shift, both still cuddled but the cat's "
        "head turns slightly toward the camera, the dog's ear flicks. Subtle "
        "movement appropriate for peaceful sleep.\n\n"
        "9:16 vertical composition. Same vtuber kawaii art style."
    ),
}


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def call_gemini(image_path: Path, prompt: str, api_key: str) -> bytes:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": image_b64}},
            ],
        }],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:500]}") from e

    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inline_data") or part.get("inlineData")
            if not inline:
                continue
            mime_resp = inline.get("mime_type") or inline.get("mimeType", "")
            if mime_resp.startswith("image/"):
                return base64.b64decode(inline["data"])

    raise RuntimeError(
        f"no image part in response: {json.dumps(payload)[:600]}"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cut", default="cut3_dance_party",
                   help="cut tag to generate end frame for "
                        "(must have a prompt in END_FRAME_PROMPTS)")
    p.add_argument("--in-dir", default=str(REGEN_DIR),
                   help=f"dir containing the start regen PNG "
                        f"(default: {_rel(REGEN_DIR)})")
    p.add_argument("--out-dir", default=str(REGEN_DIR),
                   help="dir to write the end PNG (default: same as in-dir)")
    p.add_argument("--n", type=int, default=1,
                   help="how many variants (saved as <tag>_end_v1.png ...)")
    p.add_argument("--dry-run", action="store_true",
                   help="print prompt + plan, no API call")
    args = p.parse_args()

    if args.cut not in END_FRAME_PROMPTS:
        print(f"ERROR: no end-frame prompt defined for {args.cut!r}. "
              f"Available: {list(END_FRAME_PROMPTS.keys())}",
              file=sys.stderr)
        return 2

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = in_dir / f"{args.cut}.png"
    if not src.exists():
        print(f"ERROR: start frame {_rel(src)} not found "
              f"(run regen_vtuber_style.py first)", file=sys.stderr)
        return 2

    prompt = END_FRAME_PROMPTS[args.cut]
    print(f"==> {args.cut}_end")
    print(f"    src = {_rel(src)}")

    if args.dry_run:
        print(f"    [dry-run] full prompt:")
        for line in prompt.split("\n"):
            print(f"      {line}")
        return 0

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        return 2

    failures = 0
    for i in range(1, args.n + 1):
        suffix = f"_v{i}" if args.n > 1 else ""
        out_path = out_dir / f"{args.cut}_end{suffix}.png"
        try:
            png = call_gemini(src, prompt, api_key)
        except Exception as e:
            print(f"    ! variant {i}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            failures += 1
            continue
        out_path.write_bytes(png)
        size_kb = out_path.stat().st_size / 1024
        print(f"    ok v{i} ({size_kb:.0f} KB) → {_rel(out_path)}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
