"""
scripts/motion_b_vlm.py
-----------------------
Method B: VLM (Gemini vision) check on first/last frames.

Theory: pixel-level metrics (YAVG diff) can be fooled by camera motion
(push-in / pan / zoom) and sticker animation — the whole frame moves
even when the cat/dog stays frozen. A vision-language model looking at
just two frames (first + last) can directly answer the question we
actually care about: "did the cat AND the dog visibly change pose
between these frames?"

Pipeline
--------
1. ffmpeg extracts frame 0 and the last frame as JPEGs (downsized to
   1024px on the long edge to keep tokens reasonable).
2. Both images + a structured prompt are sent to Gemini (vision-capable,
   default gemini-2.5-flash). responseMimeType=application/json forces
   clean JSON output so we skip fence-stripping.
3. Model returns JSON: {"cat_moved": bool, "dog_moved": bool,
                        "cat_evidence": "...", "dog_evidence": "...",
                        "stickers_only": bool, "camera_moved": bool}.
4. Verdict = OK iff cat_moved AND dog_moved (both subjects).

Why Gemini, not Claude: the rest of the pipeline (Veo 3 i2v) already
uses GOOGLE_API_KEY — one key, one bill, fewer footguns. Gemini Flash
vision is also ~5-10x cheaper than Claude sonnet for this workload.

Usage
-----
    python3 scripts/motion_b_vlm.py path/to/clip.mp4
    python3 scripts/motion_b_vlm.py path/to/clip.mp4 --mode either   # OK if either
    python3 scripts/motion_b_vlm.py path/to/clip.mp4 --json          # full JSON dump
    python3 scripts/motion_b_vlm.py clip1.mp4 clip2.mp4 clip3.mp4    # batch mode

Env
---
    GOOGLE_API_KEY      required (same key used by animate_hero_veo3.py)
    VLM_MODEL           override model (default: gemini-2.5-flash)

Exit codes (single-file mode)
-----------------------------
    0   verdict OK   (both subjects moved, unless --mode either)
    1   verdict FAIL (subject motion insufficient)
    2   error        (file missing, ffmpeg fail, api fail, bad json)

Exit codes (batch mode, multiple mp4s)
--------------------------------------
    0   ALL files OK
    1   any file FAIL (some animals didn't move)
    2   any file ERROR (a non-OK/non-FAIL outcome occurred)
    summary line at the end with counts.

Cost note
---------
~$0.001-0.003 per call on gemini-2.5-flash (two 1024px frames +
a few-hundred-token JSON response). Effectively free for a 5-cut episode.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    # override=True so .env wins over stale shell exports (common footgun:
    # GOOGLE_API_KEY left over in .zshrc from a previous project / gcloud).
    load_dotenv(override=True)
except ImportError:
    pass

import ssl
import urllib.request
import urllib.error

# macOS Python (especially python.org installer) often can't find a
# CA bundle, leading to "CERTIFICATE_VERIFY_FAILED" on every urlopen.
# Use certifi's bundle if available — it ships with most pip installs
# and is one `pip install certifi` away if not. Fall back to default
# context so Linux / properly-configured environments still work.
try:
    import certifi
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None


def ffprobe_duration(mp4: Path) -> float:
    """Return clip duration in seconds (float)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def extract_frame(mp4: Path, t_sec: float, out_path: Path, long_edge: int = 1024):
    """Extract a single frame at t_sec to a JPEG, scaled so long edge = long_edge."""
    # scale='if(gt(iw,ih),1024,-2)':'if(gt(iw,ih),-2,1024)'
    vf = f"scale='if(gt(iw,ih),{long_edge},-2)':'if(gt(iw,ih),-2,{long_edge})'"
    subprocess.run(
        ["ffmpeg", "-nostats", "-loglevel", "error", "-y",
         "-ss", f"{t_sec:.3f}", "-i", str(mp4),
         "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out_path)],
        check=True,
    )


def b64_jpeg(p: Path) -> str:
    return base64.standard_b64encode(p.read_bytes()).decode("ascii")


VLM_PROMPT = """You are inspecting two frames extracted from a short pet video.
Frame 1 is the FIRST frame of the clip. Frame 2 is the LAST frame.

The clip contains an orange tabby CAT and a small black French bulldog DOG.
There may also be decorative cartoon STICKERS (hearts, sparkles, text bubbles)
overlaid on the frame — IGNORE sticker movement. The camera may also push in,
pan, or zoom slightly — IGNORE camera motion. Your job is to judge whether
the ANIMALS themselves visibly changed pose between frame 1 and frame 2.

Look for changes in:
- head angle / direction the animal is facing
- eye state (open vs closed / squint vs wide)
- ear position
- mouth (closed vs open, tongue out, yawning)
- body posture (sitting up vs lying down, leaning, turning)
- tail position (cat only)
- paw/leg position

Do NOT count as motion:
- pure camera zoom / pan / push-in (the animal's pose itself is unchanged
  but it appears larger or shifted in the frame)
- sticker/text/overlay animation
- subtle JPEG noise

Respond with ONLY a JSON object, no prose, no markdown fence:
{
  "cat_moved": true | false,
  "cat_evidence": "<one short sentence describing the cat's pose change, or 'no change' if static>",
  "dog_moved": true | false,
  "dog_evidence": "<same for the dog>",
  "stickers_only": true | false,
  "camera_moved": true | false
}
"""


def call_gemini_vlm(api_key: str, model: str, frame1_b64: str, frame2_b64: str,
                    timeout: int = 60) -> dict:
    """Call Gemini generateContent with two inline JPEG frames + VLM_PROMPT.

    Schema: https://ai.google.dev/api/generate-content
    responseMimeType="application/json" forces JSON output, so we don't have
    to strip ```json fences like the Claude version did.
    """
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "FRAME 1 (first frame of the clip):"},
                    {"inline_data": {"mime_type": "image/jpeg",
                                     "data": frame1_b64}},
                    {"text": "FRAME 2 (last frame of the clip):"},
                    {"inline_data": {"mime_type": "image/jpeg",
                                     "data": frame2_b64}},
                    {"text": VLM_PROMPT},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024,
            "temperature": 0.2,
            # Gemini 2.5 family enables "thinking" by default, which silently
            # eats the output-token budget. For this kind of simple 2-frame
            # visual comparison it adds no value and just truncates the JSON
            # mid-write (you'd see finishReason=MAX_TOKENS with half a brace).
            # thinkingBudget=0 disables it; bump if a future task needs it.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"gemini api {e.code}: {body_text[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"gemini api network error: {e.reason}") from e

    # Gemini response shape:
    #   {"candidates": [{"content": {"parts": [{"text": "..."}], "role": "model"},
    #                    "finishReason": "STOP", ...}], ...}
    candidates = data.get("candidates", [])
    if not candidates:
        # surface promptFeedback (safety block, etc.) for easier debugging
        pf = data.get("promptFeedback") or {}
        raise RuntimeError(f"no candidates in response (promptFeedback={pf}): "
                           f"{json.dumps(data)[:400]}")
    cand0 = candidates[0]
    finish = cand0.get("finishReason", "")
    parts = (cand0.get("content") or {}).get("parts", [])
    text = ""
    for part in parts:
        if "text" in part:
            text += part["text"]
    if not text:
        raise RuntimeError(
            f"no text in candidate (finishReason={finish}): "
            f"{json.dumps(cand0)[:400]}")
    # If truncated, fail loudly with the reason so we can bump tokens /
    # disable thinking rather than getting a cryptic JSON parse error.
    if finish and finish not in ("STOP", "MAX_TOKENS"):
        raise RuntimeError(f"abnormal finishReason={finish}: {text[:200]}")
    if finish == "MAX_TOKENS":
        raise RuntimeError(
            f"response truncated (finishReason=MAX_TOKENS) — bump "
            f"maxOutputTokens or disable thinkingConfig. raw: {text[:200]}")
    # responseMimeType=application/json should make this clean, but strip
    # leftover fences if Gemini ever ignores the directive.
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse model JSON: {e}\nraw: {text[:400]}") from e


def check_one(mp4: Path, api_key: str, model: str, mode: str,
              dump_json: bool) -> int:
    """Run the full VLM check on one mp4. Returns 0/1/2 (see header)."""
    if not mp4.exists():
        print(f"{mp4.name}: ERROR — file not found", file=sys.stderr)
        return 2

    try:
        dur = ffprobe_duration(mp4)
    except Exception as e:
        print(f"{mp4.name}: ERROR — ffprobe failed: {e}", file=sys.stderr)
        return 2

    t_first = 0.0
    t_last = max(0.0, dur - 0.05)

    with tempfile.TemporaryDirectory() as td:
        f1 = Path(td) / "f1.jpg"
        f2 = Path(td) / "f2.jpg"
        try:
            extract_frame(mp4, t_first, f1)
            extract_frame(mp4, t_last, f2)
        except subprocess.CalledProcessError as e:
            print(f"{mp4.name}: ERROR — ffmpeg frame extract failed: {e}",
                  file=sys.stderr)
            return 2

        try:
            result = call_gemini_vlm(api_key, model,
                                     b64_jpeg(f1), b64_jpeg(f2))
        except Exception as e:
            print(f"{mp4.name}: ERROR — VLM call failed: {e}", file=sys.stderr)
            return 2

    cat_m = bool(result.get("cat_moved"))
    dog_m = bool(result.get("dog_moved"))
    cat_ev = result.get("cat_evidence", "")
    dog_ev = result.get("dog_evidence", "")
    stickers = bool(result.get("stickers_only"))
    cam_m = bool(result.get("camera_moved"))

    if mode == "both":
        ok = cat_m and dog_m
    else:
        ok = cat_m or dog_m

    verdict = "OK" if ok else "STATIC"
    print(f"{mp4.name}: cat={cat_m} dog={dog_m} camera={cam_m} "
          f"stickers_only={stickers} mode={mode}  → {verdict}")
    print(f"  cat: {cat_ev}")
    print(f"  dog: {dog_ev}")

    if dump_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mp4", nargs="+",
                   help="one or more mp4 files to check (batch mode runs sequentially)")
    p.add_argument("--mode", choices=["both", "either"], default="both",
                   help="OK if BOTH animals moved (default) or EITHER animal moved")
    p.add_argument("--json", action="store_true",
                   help="dump full VLM JSON to stdout in addition to verdict line")
    p.add_argument("--model",
                   default=os.environ.get("VLM_MODEL", "gemini-2.5-flash"),
                   help="Gemini vision model id (default: gemini-2.5-flash). "
                        "Try gemini-2.5-pro for harder cases.")
    args = p.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set "
              "(get one at https://aistudio.google.com/apikey)", file=sys.stderr)
        return 2

    paths = [Path(p) for p in args.mp4]

    # single-file mode preserves the original semantics
    if len(paths) == 1:
        return check_one(paths[0], api_key, args.model, args.mode, args.json)

    # batch mode: per-file verdict + aggregated summary
    counts = {"OK": 0, "STATIC": 0, "ERROR": 0}
    worst = 0  # 0 < 1 < 2 — worst overall rc bubbles up
    for i, mp4 in enumerate(paths, 1):
        print(f"\n[{i}/{len(paths)}] {mp4}")
        rc = check_one(mp4, api_key, args.model, args.mode, args.json)
        if rc == 0:
            counts["OK"] += 1
        elif rc == 1:
            counts["STATIC"] += 1
            if worst < 1:
                worst = 1
        else:
            counts["ERROR"] += 1
            worst = 2

    print()
    print("=" * 60)
    print(f"Summary ({len(paths)} clips, mode={args.mode}):")
    print(f"  OK     : {counts['OK']}")
    print(f"  STATIC : {counts['STATIC']}")
    print(f"  ERROR  : {counts['ERROR']}")
    print("=" * 60)
    return worst


if __name__ == "__main__":
    sys.exit(main())
