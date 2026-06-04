"""
scripts/animate_hero_veo3.py
----------------------------
Google Veo 3 image-to-video wrapper (parallel to animate_hero.py for sora-2).
Same CLI surface where possible so the comparison runner can swap generators.

Pipeline (Gemini API REST, long-running predict):
  POST {base}/models/{model}:predictLongRunning  → operation name
  GET  {base}/{operation}  (poll until done.true)
  → response contains video URI or base64-encoded mp4 bytes
  download / decode → mp4

Usage:
    python3 scripts/animate_hero_veo3.py \
        --image data/output/decorated/cut5_closer.png \
        --prompt "..." \
        --seconds 4 \
        --model veo-3.0-generate-001 \
        --output data/output/animated/test_veo3.mp4

Env:
    GOOGLE_API_KEY    — required (Gemini API key from https://aistudio.google.com/apikey)
    VEO_POLL_TIMEOUT  — total poll timeout in seconds (default 1200 / 20min)

Exit codes:
    0   success
    2   input error (file missing, key missing)
    3   generation failed / timeout / api error

NOTE — VERIFY BEFORE FIRST REAL CALL (knowledge cutoff lag):
    * Model strings: as of cutoff, Veo 3 family included
        `veo-3.0-generate-001`        (standard, with audio, ~$0.40-0.75/sec)
        `veo-3.0-fast-generate-001`   (fast, cheaper, may not have audio)
      Google may have promoted veo-3.1 or veo-4 since. Run:
        curl -H "x-goog-api-key: $GOOGLE_API_KEY" \\
             "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100" \\
             | grep -i veo
      Update --model defaults below if newer.
    * Pricing: docs.google.com/.../pricing — confirm before running --real on many cuts.
    * Request schema: instances[].image schema (bytesBase64Encoded vs gcsUri),
      parameters keys (aspectRatio / durationSeconds / personGeneration /
      negativePrompt). Verified against
      https://ai.google.dev/gemini-api/docs/video
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    # override=True so .env wins over stale shell exports
    load_dotenv(override=True)
except ImportError:
    pass

# Mac Python urllib SSL: system Python ships with a stale cert bundle, hits
# "CERTIFICATE_VERIFY_FAILED" on googleapis.com. Use certifi's bundle.
# Same fix that motion_b_vlm.py uses — keep them consistent.
try:
    import certifi
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

BASE = "https://generativelanguage.googleapis.com/v1beta"
POLL_INTERVAL = 8  # seconds between status polls


def http_json(method: str, url: str, api_key: str, body: dict | None = None,
              timeout: int = 60) -> dict:
    headers = {"x-goog-api-key": api_key, "content-type": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"veo http {e.code}: {text[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"veo network: {e.reason}") from e


def http_bytes(url: str, api_key: str | None = None, timeout: int = 120) -> bytes:
    headers = {}
    if api_key:
        headers["x-goog-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        return resp.read()


def _encode_image(image_path: Path) -> dict:
    """Pack an image path into the {bytesBase64Encoded, mimeType} dict shape
    that Veo's instances[].image / .lastFrame both use."""
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime:
        mime = "image/png"
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return {"bytesBase64Encoded": b64, "mimeType": mime}


def submit_job(api_key: str, model: str, prompt: str, image_path: Path,
               seconds: int, aspect: str, negative: str | None,
               last_image_path: Path | None = None) -> str:
    # Request schema per ai.google.dev/gemini-api/docs/video — verify keys.
    # When `lastFrame` is included alongside `image`, Veo 3.1 interpolates
    # between the two frames over the requested duration. This is the
    # workaround for "i2v output too static" — forcing a target pose at the
    # end frame guarantees motion in between.
    instance = {
        "prompt": prompt,
        "image": _encode_image(image_path),
    }
    if last_image_path is not None:
        instance["lastFrame"] = _encode_image(last_image_path)
    parameters = {
        "aspectRatio": aspect,           # "9:16" for Shorts
        "durationSeconds": seconds,
        "sampleCount": 1,
        "personGeneration": "allow_all", # VERIFY: enum may be "ALLOW_ALL" / "ALLOW_ADULT"
    }
    if negative:
        parameters["negativePrompt"] = negative

    url = f"{BASE}/models/{model}:predictLongRunning"
    body = {"instances": [instance], "parameters": parameters}
    resp = http_json("POST", url, api_key, body=body)
    op_name = resp.get("name")
    if not op_name:
        raise RuntimeError(f"no operation name in response: {resp}")
    return op_name


def poll_until_done(api_key: str, op_name: str, total_timeout: int) -> dict:
    url = f"{BASE}/{op_name}"
    deadline = time.time() + total_timeout
    while True:
        resp = http_json("GET", url, api_key)
        if resp.get("done"):
            if "error" in resp:
                err = resp["error"]
                raise RuntimeError(f"veo generation failed: {err}")
            return resp.get("response", {})
        if time.time() > deadline:
            raise TimeoutError(
                f"veo poll timeout after {total_timeout}s (op={op_name})"
            )
        time.sleep(POLL_INTERVAL)


def extract_video_bytes(response: dict, api_key: str) -> bytes:
    """Veo response carries the video either as base64 bytes or a URI.

    Real response shape (Gemini API v1beta, observed 2026-05-15):
        {"@type": "...PredictLongRunningResponse",
         "generateVideoResponse": {
            "generatedSamples": [{"video": {"uri": "https://..."}}]}}
    """
    # dig through the generateVideoResponse wrapper first (current schema),
    # then fall back to flat layouts in case Google changes it.
    gvr = response.get("generateVideoResponse") or {}
    samples = (gvr.get("generatedSamples")
               or gvr.get("videos")
               or response.get("generatedSamples")
               or response.get("videos")
               or response.get("predictions")
               or [])
    if not samples:
        raise RuntimeError(f"no samples in response: {json.dumps(response)[:400]}")
    s = samples[0]
    vid = s.get("video") or s
    if "bytesBase64Encoded" in vid:
        return base64.standard_b64decode(vid["bytesBase64Encoded"])
    if "uri" in vid:
        return http_bytes(vid["uri"], api_key=api_key)
    if "videoUri" in vid:
        return http_bytes(vid["videoUri"], api_key=api_key)
    raise RuntimeError(f"unrecognized sample shape: {json.dumps(s)[:400]}")


def write_sidecar(out_path: Path, info: dict):
    side = out_path.with_suffix(out_path.suffix + ".meta.json")
    side.write_text(json.dumps(info, indent=2, ensure_ascii=False))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True,
                   help="start frame (first frame of the clip)")
    p.add_argument("--last-image", default=None,
                   help="optional end frame — Veo 3.1 will interpolate "
                        "motion between --image and --last-image. Use for "
                        "forcing dynamic motion when default i2v is too static.")
    p.add_argument("--prompt", required=True)
    p.add_argument("--seconds", type=int, default=4)
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--negative", default=None,
                   help="negative prompt (optional)")
    p.add_argument("--model", default="veo-3.0-generate-001",
                   help="Veo model id. Override to veo-3.0-fast-generate-001 "
                        "for the cheap/fast variant. VERIFY current model strings.")
    p.add_argument("--output", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="print plan, don't call API")
    args = p.parse_args()

    img = Path(args.image)
    last_img = Path(args.last_image) if args.last_image else None
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not img.exists():
        print(f"ERROR: image {img} not found", file=sys.stderr)
        return 2
    if last_img is not None and not last_img.exists():
        print(f"ERROR: last-image {last_img} not found", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[dry-run] would POST {args.model} predictLongRunning")
        print(f"  image       = {img}")
        if last_img:
            print(f"  last-image  = {last_img}  (interpolation mode)")
        print(f"  prompt      = {args.prompt!r}")
        print(f"  secs        = {args.seconds}  aspect = {args.aspect}")
        print(f"  output      = {out}")
        return 0

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set "
              "(get one at https://aistudio.google.com/apikey)", file=sys.stderr)
        return 2

    timeout = int(os.environ.get("VEO_POLL_TIMEOUT", "1200"))
    started = time.time()
    info = {
        "generator": "veo3",
        "model": args.model,
        "image": str(img),
        "last_image": str(last_img) if last_img else None,
        "prompt": args.prompt,
        "seconds": args.seconds,
        "aspect": args.aspect,
        "negative": args.negative,
        "started_at": started,
    }

    try:
        mode = "interpolate (first+last)" if last_img else "i2v (single frame)"
        print(f"==> submitting to {args.model} — {mode} — timeout {timeout}s")
        op = submit_job(api_key, args.model, args.prompt, img,
                        args.seconds, args.aspect, args.negative,
                        last_image_path=last_img)
        info["operation"] = op
        print(f"  op = {op}")
        print(f"  polling every {POLL_INTERVAL}s...")
        resp = poll_until_done(api_key, op, timeout)
        info["raw_response_keys"] = sorted(resp.keys())
        mp4_bytes = extract_video_bytes(resp, api_key)
        out.write_bytes(mp4_bytes)
        info["bytes"] = len(mp4_bytes)
        info["finished_at"] = time.time()
        info["elapsed_sec"] = round(info["finished_at"] - started, 1)
        write_sidecar(out, info)
        print(f"  ok ({info['bytes']/1e6:.2f} MB in {info['elapsed_sec']}s)")
        print(f"  → {out}")
        return 0
    except TimeoutError as e:
        info["error"] = f"timeout: {e}"
        write_sidecar(out, info)
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        info["error"] = str(e)
        # try to detect moderation refusal patterns in the error text
        if any(s in str(e).lower() for s in (
                "blocked", "policy", "safety", "moderation")):
            info["error_kind"] = "moderation_blocked"
        write_sidecar(out, info)
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
