"""
scripts/animate_seedance_i2v.py
-------------------------------
Seedance 2.0 via BytePlus Ark API. Three generation modes.

Auth:
    export BYTEPLUS_API_KEY="ark-xxxxxxxx"

Modes (mutually exclusive — BytePlus does NOT allow mixing first_frame with reference_*):

  --mode i2v       (default — current behavior)
      --image PATH        → content[role=first_frame]
      Use when you have a finalized starting frame (GPT-generated still or
      a real photo) and want to add motion.

  --mode interp    (first+last frame interpolation)
      --image PATH        → content[role=first_frame]
      --last-frame PATH   → content[role=last_frame]
      Use when start and end poses are known and you want the model to
      interpolate the motion between them. Ideal for real_footage "fill"
      cuts where the start frame is the end of clip N and the last frame
      is the start of clip N+1.

  --mode ref       (Omni Reference, no first/last frame)
      --ref-image PATH    → content[role=reference_image]  (repeatable, up to 9)
      Use when you want character consistency across many cuts without
      pinning a specific start frame. The model invents the scene using
      the references as character anchors.

Examples:
    # Mode 1 — i2v (default)
    python3 scripts/animate_seedance_i2v.py \\
        --image data/tmp/cut1_regen.png \\
        --prompt "Leo slowly blinks and tilts head" \\
        --seconds 5 --output cut1.mp4

    # Mode 2 — interp (fill a transition between two real clips)
    python3 scripts/animate_seedance_i2v.py --mode interp \\
        --image clipA_last.png --last-frame clipB_first.png \\
        --prompt "Leo walks from the doorway to the sink" \\
        --seconds 4 --output fill_cut.mp4

    # Mode 3 — ref (multi-ref Omni Reference)
    python3 scripts/animate_seedance_i2v.py --mode ref \\
        --ref-image assets/character_ref/ryani_solo.png \\
        --ref-image assets/character_ref/leo_solo.png \\
        --prompt "Ryani and Leo sit side by side on a blue sofa..." \\
        --seconds 5 --output cut.mp4
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
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
    import ssl
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

# Volcengine Ark API (global endpoint)
API_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"
POLL_INTERVAL = 8
DEFAULT_MODEL = "dreamina-seedance-2-0-260128"
FAST_MODEL = "dreamina-seedance-2-0-fast-260128"


def get_api_key() -> str:
    key = os.environ.get("BYTEPLUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BYTEPLUS_API_KEY not set (ark-xxx key from Volcengine)")
    return key


def http_json(method: str, url: str, api_key: str,
              body: dict | None = None, timeout: int = 60) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark HTTP {e.code}: {text[:600]}") from e


def http_bytes(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        return resp.read()


def encode_image_data_url(image_path: Path) -> str:
    """Encode image as base64 data URL."""
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime:
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _image_block(path: Path, role: str) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": encode_image_data_url(path)},
        "role": role,
    }


def encode_video_data_url(video_path: Path) -> str:
    """Same as encode_image_data_url but for video files. Seedance 2.0 R2V
    accepts up to 3 videos as references with role='environment' / 'motion'."""
    import mimetypes
    mime, _ = mimetypes.guess_type(str(video_path))
    if not mime:
        mime = "video/mp4"
    b64 = base64.standard_b64encode(video_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _video_block(path_or_url: "Path | str",
                  role: str = "reference_video") -> dict:
    """BytePlus Seedance R2V API expects role='reference_video' for video
    references AND requires a public HTTP(S) URL (does NOT accept base64
    data URLs for video — verified by API error 2026-06-01).

    Pass either a Path (we'll error out — video must be hosted) or a string
    URL starting with http/https.
    """
    if isinstance(path_or_url, str) and path_or_url.startswith(("http://", "https://")):
        url = path_or_url
    else:
        raise ValueError(
            "Seedance R2V requires a public HTTPS URL for reference_video. "
            "Local files cannot be base64'd (API rejects). Host the video on "
            "Google Drive / iCloud / S3 / BytePlus TOS and pass the URL."
        )
    return {
        "type": "video_url",
        "video_url": {"url": url},
        "role": role,
    }


def submit_job(api_key: str, model: str, *,
               mode: str,
               prompt: str,
               seconds: int,
               image: Path | None = None,
               last_frame: Path | None = None,
               ref_images: list[Path] | None = None,
               ref_videos: "list[tuple[str, str]] | None" = None,
               camera: str | None = None,
               ratio: str = "9:16",
               generate_audio: bool = False,
               camera_fixed: bool = False) -> str:
    """Submit Seedance task. Returns task_id.

    mode:
      "i2v"    — image (role=first_frame) only.
      "interp" — image (first_frame) + last_frame.
      "ref"    — ref_images list (role=reference_image). NO first_frame.

    BytePlus mixing rule: first_frame/last_frame and reference_* cannot
    coexist in one call.
    """
    if mode not in ("i2v", "interp", "ref"):
        raise ValueError(f"unknown mode: {mode}")

    # Duration constraint: BytePlus rejects duration!=5 for the fast model with
    # HTTP 400 "InvalidParameter". EMPIRICALLY (PD 2026-06-11, the 6/12 AV cut4
    # failure) fast REJECTS duration=4 in i2v too — NOT just ref. The earlier note
    # that fast i2v accepts 3/4/5 was WRONG; fast accepts ONLY 5 in both ref AND
    # i2v. So clamp BOTH to exactly 5. Episode cut length is set downstream by the
    # Step 3b speed-retime (AV_CUT_OUTPUT_SECONDS), NOT by the Seedance duration —
    # so always asking for 5 here is free. Observed-valid set:
    #   fast ref / i2v : 5 ONLY
    #   fast interp    : ≤4 (gap-fill, first+last frame — different content config)
    #   standard       : 3-12 (full range)
    if "fast" in model:
        if mode in ("ref", "i2v") and seconds != 5:
            print(f"WARN: fast model in {mode} mode supports only 5s — "
                  f"clamping seconds={seconds} → 5", file=sys.stderr)
            seconds = 5
        elif seconds > 5:
            print(f"WARN: fast model max 5s — clamping seconds={seconds} → 5",
                  file=sys.stderr)
            seconds = 5

    content: list[dict] = [{"type": "text", "text": prompt}]

    if mode == "i2v":
        if image is None:
            raise ValueError("mode=i2v requires --image")
        content.append(_image_block(image, "first_frame"))
    elif mode == "interp":
        if image is None or last_frame is None:
            raise ValueError("mode=interp requires both --image (first) and --last-frame")
        content.append(_image_block(image, "first_frame"))
        content.append(_image_block(last_frame, "last_frame"))
    elif mode == "ref":
        if not ref_images and not ref_videos:
            raise ValueError("mode=ref requires at least one --ref-image or --ref-video")
        if ref_images and len(ref_images) > 9:
            raise ValueError(f"mode=ref: max 9 reference images, got {len(ref_images)}")
        if ref_videos and len(ref_videos) > 3:
            raise ValueError(f"mode=ref: max 3 reference videos, got {len(ref_videos)}")
        for p in (ref_images or []):
            content.append(_image_block(p, "reference_image"))
        for vp, role in (ref_videos or []):
            content.append(_video_block(vp, role))

    body: dict = {
        "model": model,
        "content": content,
        "ratio": ratio,
        "duration": seconds,
        "watermark": False,
        "generate_audio": generate_audio,
    }
    # camera_fixed: boolean param discovered via web search 2026-06-01.
    # When true, camera position is locked = spatial frame stays consistent
    # across the clip. Major candidate for solving bg/anchor drift problems.
    if camera_fixed:
        body["camera_fixed"] = True
    # camera_motion: NOT a documented Seedance 2.0 body parameter. Camera
    # control is expressed in the prompt text (e.g. "Camera pushes in slowly").
    # We dropped the body field; the --camera CLI flag is kept as a no-op for
    # backward compatibility but issues a warning at call time.

    resp = http_json("POST", f"{API_BASE}/contents/generations/tasks",
                     api_key, body=body)
    task_id = resp.get("id") or resp.get("task_id") or resp.get("job_id")
    if not task_id:
        raise RuntimeError(f"No task_id in response: {json.dumps(resp, default=str)[:500]}")
    return task_id


def poll_until_done(api_key: str, task_id: str, total_timeout: int) -> str:
    """Poll until task completes, return video URL."""
    deadline = time.time() + total_timeout
    while True:
        resp = http_json("GET",
                         f"{API_BASE}/contents/generations/tasks/{task_id}",
                         api_key)
        status = resp.get("status", "")

        if status == "succeeded":
            # Extract video URL from response
            video_url = None
            content = resp.get("content", {})
            if isinstance(content, dict):
                video_url = content.get("video_url")
            if not video_url:
                # Try other response shapes
                video_url = (resp.get("video_url")
                             or resp.get("output", {}).get("video_url"))
            if not video_url:
                raise RuntimeError(f"Succeeded but no video URL: {json.dumps(resp, default=str)[:500]}")
            return video_url

        if status in ("failed", "error", "cancelled", "expired"):
            error_msg = resp.get("error", resp.get("message", str(resp)))
            raise RuntimeError(f"Task {status}: {error_msg}")

        if time.time() > deadline:
            raise TimeoutError(f"Timeout after {total_timeout}s (task={task_id})")

        time.sleep(POLL_INTERVAL)


def main() -> int:
    p = argparse.ArgumentParser(description="Seedance 2.0 via BytePlus Ark")
    p.add_argument("--mode", choices=["i2v", "interp", "ref"], default="i2v",
                   help="i2v=first_frame only; interp=first+last frame; ref=Omni Reference (no first_frame)")
    p.add_argument("--image", default=None,
                   help="first-frame image (required for i2v and interp)")
    p.add_argument("--last-frame", default=None,
                   help="last-frame image (interp only)")
    p.add_argument("--ref-image", action="append", default=[],
                   help="reference image for ref mode (repeatable, up to 9)")
    p.add_argument("--ref-video", action="append", default=[],
                   help="reference video for ref mode — format PATH or PATH:role "
                        "(role=environment|motion, default environment). Repeatable, up to 3.")
    p.add_argument("--prompt", required=True, help="motion/scene description")
    p.add_argument("--seconds", type=int, default=5)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--fast", action="store_true", help="use fast (720p) model")
    p.add_argument("--ratio", default="9:16", help="aspect ratio (default 9:16 for Shorts)")
    p.add_argument("--generate-audio", action="store_true",
                   help="let Seedance produce an audio track (default: off — we use external BGM)")
    p.add_argument("--camera", default=None,
                   help="(NO-OP — Seedance 2.0 expresses camera in prompt text. This flag is ignored.)")
    p.add_argument("--camera-fixed", action="store_true",
                   help="Lock camera position (boolean Seedance API param). "
                        "Holds spatial frame stable across the clip; pairs well "
                        "with ref mode + reference_video for cross-cut anchor.")
    # Deprecated — kept so old callers don't break; routed to --ref-image when mode=ref
    p.add_argument("--subject-ref", default=None,
                   help="(DEPRECATED — use --mode ref --ref-image PATH instead)")
    p.add_argument("--output", required=True)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    # ── Validate mode-specific inputs ──
    image: Path | None = None
    last_frame: Path | None = None
    ref_images: list[Path] = []

    if args.mode in ("i2v", "interp"):
        if not args.image:
            print(f"ERROR: --image required for mode={args.mode}", file=sys.stderr)
            return 2
        image = Path(args.image)
        if not image.exists():
            print(f"ERROR: image {image} not found", file=sys.stderr)
            return 2

    if args.mode == "interp":
        if not args.last_frame:
            print("ERROR: --last-frame required for mode=interp", file=sys.stderr)
            return 2
        last_frame = Path(args.last_frame)
        if not last_frame.exists():
            print(f"ERROR: last-frame {last_frame} not found", file=sys.stderr)
            return 2

    ref_videos: "list[tuple[str, str]]" = []
    if args.mode == "ref":
        # Accept legacy --subject-ref as a single ref
        refs = list(args.ref_image)
        if args.subject_ref and args.subject_ref not in refs:
            refs.append(args.subject_ref)
        for r in refs:
            rp = Path(r)
            if not rp.exists():
                print(f"ERROR: ref image {rp} not found", file=sys.stderr)
                return 2
            ref_images.append(rp)
        # Parse --ref-video URL (API requires HTTP/HTTPS URL — base64 rejected).
        # Optional :role suffix supported but only 'reference_video' valid.
        for raw in (args.ref_video or []):
            if raw.startswith(("http://", "https://")):
                ref_videos.append((raw, "reference_video"))
            else:
                print(f"ERROR: --ref-video must be a public HTTPS URL "
                      f"(got: {raw!r}). Seedance R2V doesn't accept local files "
                      "or base64 video data — host on Google Drive / iCloud / S3.",
                      file=sys.stderr)
                return 2
        if not ref_images and not ref_videos:
            print("ERROR: mode=ref requires at least one --ref-image or --ref-video",
                  file=sys.stderr)
            return 2

    if args.camera:
        print(
            f"WARN: --camera={args.camera} is a no-op for Seedance 2.0. "
            "Express camera motion in the prompt text instead.",
            file=sys.stderr,
        )

    if args.mode != "ref" and args.subject_ref:
        # BytePlus mixing constraint: ref cannot coexist with first_frame
        print(
            f"WARN: --subject-ref ignored in mode={args.mode} "
            "(Seedance API does not allow mixing first_frame with reference_*). "
            "Use --mode ref to use reference images.",
            file=sys.stderr,
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    model = FAST_MODEL if args.fast else args.model

    if args.dry_run:
        print(f"[dry-run] Seedance 2.0 ({args.mode}) — BytePlus Ark")
        print(f"  model  = {model}")
        if image:
            print(f"  image  = {image} ({image.stat().st_size/1e3:.0f}KB) role=first_frame")
        if last_frame:
            print(f"  last   = {last_frame} ({last_frame.stat().st_size/1e3:.0f}KB) role=last_frame")
        for r in ref_images:
            print(f"  ref    = {r} ({r.stat().st_size/1e3:.0f}KB) role=reference_image")
        print(f"  prompt = {args.prompt!r}")
        print(f"  secs   = {args.seconds}  ratio = {args.ratio}  camera = {args.camera or 'auto'}")
        print(f"  output = {out}")
        return 0

    api_key = get_api_key()

    started = time.time()
    info = {
        "generator": f"seedance-2.0-{args.mode}",
        "model": model,
        "mode": args.mode,
        "image": str(image) if image else None,
        "last_frame": str(last_frame) if last_frame else None,
        "ref_images": [str(r) for r in ref_images],
        "prompt": args.prompt,
        "seconds": args.seconds,
        "camera": args.camera,
        "started_at": started,
    }

    try:
        print(f"==> Seedance {model} mode={args.mode} — {args.seconds}s")
        task_id = submit_job(
            api_key, model,
            mode=args.mode,
            prompt=args.prompt,
            seconds=args.seconds,
            image=image,
            last_frame=last_frame,
            ref_images=ref_images,
            ref_videos=ref_videos,
            camera=args.camera,
            ratio=args.ratio,
            generate_audio=args.generate_audio,
            camera_fixed=args.camera_fixed,
        )
        info["task_id"] = task_id
        print(f"  task = {task_id}")
        print(f"  polling every {POLL_INTERVAL}s...")

        video_url = poll_until_done(api_key, task_id, args.timeout)
        mp4_bytes = http_bytes(video_url)
        out.write_bytes(mp4_bytes)

        info["bytes"] = len(mp4_bytes)
        info["finished_at"] = time.time()
        info["elapsed_sec"] = round(info["finished_at"] - started, 1)

        side = out.with_suffix(out.suffix + ".meta.json")
        side.write_text(json.dumps(info, indent=2, ensure_ascii=False))

        print(f"  ok ({info['bytes']/1e6:.2f} MB in {info['elapsed_sec']}s)")
        print(f"  → {out}")
        return 0

    except TimeoutError as e:
        info["error"] = f"timeout: {e}"
        side = out.with_suffix(out.suffix + ".meta.json")
        side.write_text(json.dumps(info, indent=2, ensure_ascii=False))
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        info["error"] = str(e)
        side = out.with_suffix(out.suffix + ".meta.json")
        side.write_text(json.dumps(info, indent=2, ensure_ascii=False))
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
