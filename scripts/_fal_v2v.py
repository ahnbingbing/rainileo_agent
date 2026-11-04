"""Video-to-video Halloween FILTER restyle via fal.ai Kling O3 (preserves the real motion).
Uploads a source cut, applies a premium cinematic Halloween filter by prompt, downloads the result.
PD workflow: 1-cut sample first → confirm → batch the rest. Needs FAL_KEY in .env."""
import sys, ssl, urllib.request
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())   # Mac urllib SSL fix (gotcha #3)
except Exception:
    _CTX = ssl.create_default_context()
import fal_client

MODEL = "fal-ai/kling-video/o3/pro/video-to-video/edit"
PROMPT = (
    "Keep the real photographic cat and dog in @Video1 COMPLETELY UNCHANGED — same real fur, same "
    "faces, same bodies, same real little bat-wing costumes, same exact motion. DO NOT stylize, "
    "cartoonify, 3D-render, smooth, or redraw the animals in any way; they must stay photorealistic, "
    "exactly as filmed. ONLY change the environment AROUND them: add a subtle moody Halloween "
    "atmosphere to the background and lighting — a gentle deep-purple and warm-orange color grade, "
    "soft darkened edges and light haze, and tasteful background Halloween elements that sit BEHIND "
    "the pets: a few distant bats flying, faint glowing cobwebs in the top corners, a soft glowing "
    "jack-o-lantern in the background, and gentle floating embers. The pets themselves and their "
    "motion stay 100% real and untouched; this is a light atmospheric color/background filter only, "
    "not a restyle of the subjects."
)


def main():
    src = Path(sys.argv[1]); out = Path(sys.argv[2])
    print("uploading", src, "...")
    url = fal_client.upload_file(str(src))
    print("uploaded ->", url[:80], "...\nsubmitting to", MODEL)
    result = fal_client.subscribe(
        MODEL,
        arguments={"video_url": url, "prompt": PROMPT, "keep_audio": False},
        with_logs=True,
        on_queue_update=lambda u: [print("  ", getattr(l, "message", l)) for l in getattr(u, "logs", []) or []],
    )
    vid = (result.get("video") or {}).get("url") or result.get("video_url")
    if not vid:
        print("NO VIDEO in result:", str(result)[:400]); return 1
    out.write_bytes(urllib.request.urlopen(vid, timeout=300, context=_CTX).read())
    print("OK ->", out, out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
