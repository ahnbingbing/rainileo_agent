"""Video-to-video seasonal/theme FILTER restyle via fal.ai Kling O3 (preserves the real motion).
Uploads a source cut, applies a background/atmosphere filter by prompt (subjects kept photoreal),
downloads the result. PD workflow: 1-cut sample first → confirm → batch the rest. Needs FAL_KEY in .env.

Prompt resolution (the filter theme is NOT fixed — Kling has no strength knob, so the prompt MUST force
subject preservation or it CG-redraws the pets): argv[3] > env FAL_V2V_PROMPT > the default Halloween one.
Pre-baked themes live in PROMPTS (e.g. 'snow' = add falling snow outside a window)."""
import os, sys, ssl, urllib.request
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
_SUBJECT_LOCK = (
    "Keep the real photographic pet(s) in @Video1 COMPLETELY UNCHANGED — same real fur, same faces, "
    "same bodies, same exact motion. DO NOT stylize, cartoonify, 3D-render, smooth, or redraw the "
    "animal(s) in any way; they must stay photorealistic, exactly as filmed. ")
PROMPTS = {
    "halloween": (_SUBJECT_LOCK +
        "ONLY change the environment AROUND them: a subtle moody Halloween atmosphere — gentle "
        "deep-purple and warm-orange color grade, soft darkened edges and light haze, and tasteful "
        "background Halloween elements BEHIND the pets (a few distant bats, faint glowing cobwebs in "
        "the corners, a soft glowing jack-o-lantern, gentle floating embers). A light atmospheric "
        "color/background filter only, not a restyle of the subjects."),
    "snow": (_SUBJECT_LOCK +
        "ONLY change what is seen OUTSIDE the window in the background: turn it into a gentle winter "
        "scene with big soft snowflakes falling steadily outside the glass, snow-dusted trees/rooftops "
        "in the distance, and soft cool winter daylight. The room/interior and the pet(s) stay exactly "
        "as filmed — only the view through the window becomes a snowy first-snow day. A light "
        "background/atmosphere filter only, not a restyle of the subjects."),
}
PROMPT = PROMPTS["halloween"]  # default (back-compat)


def main():
    src = Path(sys.argv[1]); out = Path(sys.argv[2])
    arg_prompt = sys.argv[3] if len(sys.argv) > 3 else None
    prompt = arg_prompt or os.getenv("FAL_V2V_PROMPT") or PROMPTS.get(os.getenv("FAL_V2V_THEME", ""), PROMPT)
    print("uploading", src, "...")
    url = fal_client.upload_file(str(src))
    print("uploaded ->", url[:80], "...\nsubmitting to", MODEL)
    result = fal_client.subscribe(
        MODEL,
        arguments={"video_url": url, "prompt": prompt, "keep_audio": False},
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
