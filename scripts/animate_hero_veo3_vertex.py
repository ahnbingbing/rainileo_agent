"""
scripts/animate_hero_veo3_vertex.py
-----------------------------------
Vertex AI variant of animate_hero_veo3.py. Same CLI surface but talks to
Vertex AI Veo endpoint instead of Gemini API.

Why this exists
---------------
Gemini API's Veo (the one keyed by GOOGLE_API_KEY) is a simplified surface
that DOES NOT expose `instances[].lastFrame` — needed for first+last frame
interpolation, which is our workaround for "i2v output too static". Vertex
AI's Veo endpoint exposes the full Veo feature set including lastFrame.

Trade-offs vs Gemini API:
  + lastFrame interpolation supported
  + access to all Veo features as Google releases them
  + clearer regional / billing accounting
  - heavier auth: requires gcloud SDK + GCP_PROJECT
  - billing must be enabled on the project (vs Gemini API's free tier)
  - separate endpoint per region

Auth
----
Uses Application Default Credentials (ADC). Set up once:
    brew install --cask google-cloud-sdk
    gcloud auth login
    gcloud auth application-default login
    gcloud config set project <YOUR_PROJECT_ID>
    gcloud services enable aiplatform.googleapis.com

Then this script shells out to `gcloud auth print-access-token` for a short-
lived Bearer token each invocation. No service-account JSON file needed.

Env
---
    GCP_PROJECT       — required. Your GCP project ID (the one with Vertex AI
                        + billing enabled).
    GCP_REGION        — optional. Default us-central1.
    VEO_POLL_TIMEOUT  — optional. Total poll deadline in seconds (default 1200).

Usage (matches animate_hero_veo3.py)
------------------------------------
    python3 scripts/animate_hero_veo3_vertex.py \
        --image data/tmp/episode_02_regen/cut3_dance_party.png \
        --last-image data/tmp/episode_02_regen/cut3_dance_party_end.png \
        --prompt "..." \
        --seconds 4 \
        --model veo-3.0-generate-001 \
        --output data/output/animated/cut3_dance_party.mp4

Models with documented first+last frame support on Vertex AI:
    veo-3.0-generate-001       ← recommended default
    veo-3.0-fast-generate-001  ← cheaper / faster, also supports lastFrame
    veo-3.1-generate-preview   ← may or may not, retry if first fails
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
    load_dotenv(override=True)
except ImportError:
    pass

try:
    import certifi
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

POLL_INTERVAL = 8


def vertex_base(project: str, region: str) -> str:
    return f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}"


def get_access_token() -> str:
    """Get a short-lived Bearer token via ADC. Shells out to gcloud.

    Why gcloud instead of google-auth Python lib: avoids adding a heavy dep
    (google-auth pulls cryptography, etc.) for a one-line shell call. The
    token lasts ~1h which is plenty for our 4s clips.
    """
    try:
        r = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, check=True, timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "gcloud CLI not found. Install: brew install --cask google-cloud-sdk"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"gcloud auth print-access-token failed: {e.stderr.strip()}\n"
            f"Did you run `gcloud auth application-default login`?"
        )
    token = r.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned empty access token")
    return token


def http_json(method: str, url: str, token: str, body: dict | None = None,
              timeout: int = 60) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"vertex http {e.code}: {text[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"vertex network: {e.reason}") from e


def http_bytes(url: str, token: str | None = None, timeout: int = 120) -> bytes:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        return resp.read()


def encode_image(image_path: Path) -> dict:
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime:
        mime = "image/png"
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return {"bytesBase64Encoded": b64, "mimeType": mime}


def submit_job(token: str, project: str, region: str, model: str,
               prompt: str, image_path: Path | None, last_image_path: Path | None,
               seconds: int, aspect: str, negative: str | None) -> str:
    instance: dict = {"prompt": prompt}
    # image-to-video (with reference) or text-to-video (prompt only)
    if image_path is not None:
        instance["image"] = encode_image(image_path)
    if last_image_path is not None:
        instance["lastFrame"] = encode_image(last_image_path)

    parameters: dict = {
        "aspectRatio": aspect,
        "durationSeconds": seconds,
        "sampleCount": 1,
        "personGeneration": "allow_adult",  # Vertex enum is different from Gemini
    }
    if negative:
        parameters["negativePrompt"] = negative

    url = (f"{vertex_base(project, region)}/publishers/google/models/"
           f"{model}:predictLongRunning")
    body = {"instances": [instance], "parameters": parameters}
    resp = http_json("POST", url, token, body=body)
    op_name = resp.get("name")
    if not op_name:
        raise RuntimeError(f"no operation name in response: {resp}")
    return op_name


def poll_until_done(token: str, project: str, region: str, model: str,
                    op_name: str, total_timeout: int) -> dict:
    """Poll a Veo Vertex AI long-running op.

    IMPORTANT: Vertex AI's Veo long-running ops can NOT be polled via GET on
    the op URL directly (that returns a generic 404 HTML page). Instead you
    POST `{model}:fetchPredictOperation` with `{"operationName": op_name}`.
    This is documented under Vertex AI Veo but easy to miss — most other
    Vertex AI long-running APIs DO support direct GET, so the parallel is
    misleading.
    """
    url = (f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/"
           f"locations/{region}/publishers/google/models/{model}"
           f":fetchPredictOperation")
    body = {"operationName": op_name}
    deadline = time.time() + total_timeout
    while True:
        resp = http_json("POST", url, token, body=body)
        if resp.get("done"):
            if "error" in resp:
                raise RuntimeError(f"vertex generation failed: {resp['error']}")
            return resp.get("response", {})
        if time.time() > deadline:
            raise TimeoutError(
                f"vertex poll timeout after {total_timeout}s (op={op_name})"
            )
        time.sleep(POLL_INTERVAL)


def extract_video_bytes(response: dict, token: str) -> bytes:
    """Vertex response shape (Veo on aiplatform.googleapis.com, 2026 spec):
        {"@type": "...PredictLongRunningResponse",
         "videos": [{"bytesBase64Encoded": "..."}]}  OR
        {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "..."}}]}}
    Same dig pattern as the Gemini variant.
    """
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
    for k in ("uri", "videoUri", "gcsUri"):
        if k in vid:
            return http_bytes(vid[k], token=token)
    raise RuntimeError(f"unrecognized sample shape: {json.dumps(s)[:400]}")


def write_sidecar(out_path: Path, info: dict):
    side = out_path.with_suffix(out_path.suffix + ".meta.json")
    side.write_text(json.dumps(info, indent=2, ensure_ascii=False))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--image", default=None, help="start frame (optional — omit for text-to-video)")
    p.add_argument("--last-image", default=None,
                   help="optional end frame for first+last interpolation")
    p.add_argument("--prompt", required=True)
    p.add_argument("--seconds", type=int, default=4)
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--negative", default=None)
    p.add_argument("--model", default="veo-3.0-generate-001",
                   help="Vertex Veo model (default veo-3.0-generate-001 — "
                        "documented support for lastFrame)")
    p.add_argument("--region", default=None,
                   help="GCP region (default $GCP_REGION or us-central1)")
    p.add_argument("--project", default=None,
                   help="GCP project ID (default $GCP_PROJECT)")
    p.add_argument("--output", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    project = args.project or os.environ.get("GCP_PROJECT", "").strip()
    if not project:
        print("ERROR: GCP_PROJECT not set (export or .env)", file=sys.stderr)
        return 2
    region = args.region or os.environ.get("GCP_REGION", "us-central1").strip()

    img = Path(args.image) if args.image else None
    last_img = Path(args.last_image) if args.last_image else None
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if img is not None and not img.exists():
        print(f"ERROR: image {img} not found", file=sys.stderr)
        return 2
    if last_img is not None and not last_img.exists():
        print(f"ERROR: last-image {last_img} not found", file=sys.stderr)
        return 2

    if img is None:
        mode = "text-to-video (no image)"
    elif last_img:
        mode = "interpolate (first+last)"
    else:
        mode = "i2v (single frame)"
    if args.dry_run:
        print(f"[dry-run] would POST Vertex {args.model} predictLongRunning")
        print(f"  project = {project}")
        print(f"  region  = {region}")
        print(f"  mode    = {mode}")
        print(f"  image   = {img or '(text-to-video)'}")
        if last_img:
            print(f"  last    = {last_img}")
        print(f"  prompt  = {args.prompt!r}")
        print(f"  secs    = {args.seconds}  aspect = {args.aspect}")
        print(f"  output  = {out}")
        return 0

    timeout = int(os.environ.get("VEO_POLL_TIMEOUT", "1200"))
    started = time.time()
    info = {
        "generator": "vertex-veo3",
        "model": args.model,
        "project": project,
        "region": region,
        "image": str(img),
        "last_image": str(last_img) if last_img else None,
        "prompt": args.prompt,
        "seconds": args.seconds,
        "aspect": args.aspect,
        "negative": args.negative,
        "started_at": started,
    }

    try:
        token = get_access_token()
        print(f"==> Vertex {args.model} — {mode} — timeout {timeout}s")
        op = submit_job(token, project, region, args.model, args.prompt,
                        img, last_img, args.seconds, args.aspect, args.negative)
        info["operation"] = op
        print(f"  op = {op}")
        print(f"  polling every {POLL_INTERVAL}s...")
        resp = poll_until_done(token, project, region, args.model, op, timeout)
        info["raw_response_keys"] = sorted(resp.keys())
        mp4_bytes = extract_video_bytes(resp, token)
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
        write_sidecar(out, info)
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
