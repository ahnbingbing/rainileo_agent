"""Render the AV episode '랴니의 첫 수영 도전기' from the Giri-picked stills.

still→Seedance method (drift-bounded): each cut's Giri-picked WIN still →
Seedance i2v (5s, 9:16, no audio) → burn KO/EN captions → assemble with shared
bumpers + BGM. PD gave explicit $-OK (2026-06-15).

  .venv/bin/python -m scripts.render_first_swim
"""
import json, os, subprocess, sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONCEPT = ROOT / "data/tmp/first_swim_stills/concept.json"
STILLS = ROOT / "data/tmp/first_swim_stills"
WORK = ROOT / "data/tmp/first_swim_render"
ANIM = WORK / "animated"
CAPTIONED = WORK / "captioned"
CAPS_MANIFEST = ROOT / "scripts/prompts/episode_first_swim_captions.json"
BGM = ROOT / "assets/bgm/aliceurbandruid-happy-whistle-travel-445333.mp3"
INTRO = ROOT / "assets/branding/intro_bumper.mp4"
OUTRO = ROOT / "assets/branding/outro_bumper.mp4"
SECONDS = 5  # fast model = 5s only; pacing tuned later (no re-Seedance)


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def build_caps(concept) -> Path:
    """captions manifest: {tag: {scenes:[{start,end,ko,en}]}} in cut order."""
    out = {"_episode_id": "first_swim", "_caption_position": "bottom"}
    for cut in concept["cuts"]:
        tag = cut["tag"]
        caps = [c for c in (cut.get("captions") or []) if c.get("ko")]
        if not caps:
            continue
        n = len(caps)
        span = (4.7 - 0.3) / n
        scenes = []
        for i, c in enumerate(caps):
            scenes.append({"start": round(0.3 + i * span, 2),
                           "end": round(0.3 + (i + 1) * span, 2),
                           "ko": c["ko"], "en": c.get("en", "")})
        out[tag] = {"scenes": scenes}
    CAPS_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    CAPS_MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return CAPS_MANIFEST


def main():
    api_key = os.environ["BYTEPLUS_API_KEY"] if os.getenv("BYTEPLUS_API_KEY") else os.environ.get("ARK_API_KEY", "")
    concept = json.loads(CONCEPT.read_text(encoding="utf-8"))
    ANIM.mkdir(parents=True, exist_ok=True)
    CAPTIONED.mkdir(parents=True, exist_ok=True)

    # 1) Seedance i2v per cut from the Giri-picked WIN still.
    for cut in concept["cuts"]:
        tag = cut["tag"]
        win = STILLS / f"{tag}_WIN.png"
        out = ANIM / f"{tag}.mp4"
        if not win.exists():
            print(f"!! missing WIN still for {tag}: {win}", file=sys.stderr); sys.exit(1)
        if out.exists() and out.stat().st_size > 0:
            print(f"== {tag} already animated, skip", flush=True); continue
        run([sys.executable, "scripts/animate_seedance_i2v.py", "--mode", "i2v",
             "--image", str(win), "--prompt", cut["motion_prompt"],
             "--seconds", str(SECONDS), "--output", str(out)])
        print(f"== {tag} animated", flush=True)

    # 2) captions manifest + burn (reuse cameraman fit logic via burn_captions).
    caps = build_caps(concept)
    run([sys.executable, "scripts/burn_captions.py", "--manifest", str(caps),
         "--in-dir", str(ANIM), "--out-dir", str(CAPTIONED)])

    # 3) assemble: bumpers + BGM. assemble normalizes 1080x1920 + SAR per cut.
    final = ROOT / f"data/output/episodes/episode_av_first_swim.mp4"
    run([sys.executable, "scripts/assemble_episode.py",
         "--captions", str(caps), "--in-dir", str(CAPTIONED),
         "--music", str(BGM), "--intro-bumper", str(INTRO),
         "--outro-bumper", str(OUTRO), "--out", str(final)])
    print("\nFINAL:", final, flush=True)


if __name__ == "__main__":
    main()
