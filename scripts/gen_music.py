#!/usr/bin/env python3
"""scripts/gen_music.py — generate grammar BGM with Vertex AI Lyria (google/lyria-002).

The 93-track royalty-free library has no club/EDM banger, so the velocity ("club") edit
rode a chill-house default that read too calm (PD 2026-09-20). Rather than source + audition
+ VM-transfer a CC0 track, we GENERATE a custom instrumental tailored to each grammar. Lyria
runs on the same Vertex project already used for Veo (no new key), returns a ~32s 48kHz stereo
WAV per call.

Output flows through the drop-in convention wired in scripts/impact_edit._grammar_music:
a file at assets/bgm/<grammar>_music.mp3 overrides that grammar's default music with no code
or env change. So:

  # generate 3 velocity candidates to audition, then keep the best as the live track
  .venv/bin/python scripts/gen_music.py --grammar velocity --count 3
  #   → assets/bgm/velocity_music_v1.mp3 .. _v3.mp3   (audition, pick one)
  cp assets/bgm/velocity_music_v2.mp3 assets/bgm/velocity_music.mp3   # promote → live

  # or write straight to the live filename
  .venv/bin/python scripts/gen_music.py --grammar velocity --promote

  # free-form prompt
  .venv/bin/python scripts/gen_music.py --prompt "dreamy lofi, warm keys" --out /tmp/x.mp3

VM note: assets/bgm/ is not git-deployed on its own — after promoting a track, mirror it to
the render host the same way the existing library got there (see assets/sfx/README.md).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

REGION = os.getenv("LYRIA_REGION", "us-central1")
MODEL = "lyria-002"
BGM = ROOT / "assets" / "bgm"

# Per-grammar music briefs. velocity is the one the library can't cover; meme/story have decent
# library tracks but are here so a matched custom track is one command away. Keep prompts about
# ENERGY + INSTRUMENTATION, always instrumental (burned captions + optional TTS carry meaning).
GRAMMAR_PROMPTS = {
    "velocity": {
        "prompt": ("high-energy EDM club banger, 128 BPM four-on-the-floor kick, driving "
                   "sidechained bassline, bright plucky synth stabs, a rising riser into a big "
                   "festival drop, energetic and playful, punchy and danceable, instrumental"),
        "negative": "vocals, lyrics, slow, lo-fi, ambient, sad, sparse",
    },
    "meme": {
        "prompt": ("quirky bouncy comedic groove, snappy off-beat plucks, playful pizzicato and "
                   "woodblocks, light and mischievous, meme-video energy, upbeat, instrumental"),
        "negative": "vocals, lyrics, dark, epic, slow, ambient",
    },
    "story": {
        "prompt": ("warm cinematic cozy instrumental, gentle piano and soft strings, hopeful and "
                   "heartfelt, light rhythmic pulse under a narrated pet story, instrumental"),
        "negative": "vocals, lyrics, aggressive, EDM, harsh, distorted",
    },
}


def _token() -> str:
    try:
        r = subprocess.run(["gcloud", "auth", "print-access-token"],
                           capture_output=True, text=True, check=True, timeout=20)
    except FileNotFoundError:
        raise RuntimeError("gcloud CLI not found (brew install --cask google-cloud-sdk)")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"gcloud auth print-access-token failed: {e.stderr.strip()}\n"
                           "Did you run `gcloud auth application-default login`?")
    return r.stdout.strip()


def _ff() -> str:
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.exists(c):
            return c
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


def generate_wav(prompt: str, out_wav: Path, *, negative: str = "", seed: int | None = None) -> Path:
    """One Lyria call → a ~32s 48kHz stereo WAV at out_wav."""
    project = os.getenv("GCP_PROJECT")
    if not project:
        raise RuntimeError("GCP_PROJECT not set")
    url = (f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{project}/locations/"
           f"{REGION}/publishers/google/models/{MODEL}:predict")
    inst = {"prompt": prompt}
    if negative:
        inst["negative_prompt"] = negative
    if seed is not None:
        inst["seed"] = seed
    body = json.dumps({"instances": [inst], "parameters": {"sample_count": 1}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=180, context=_CTX)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Lyria HTTP {e.code}: {e.read().decode()[:400]}")
    preds = (json.loads(resp.read()).get("predictions") or [])
    if not preds:
        raise RuntimeError("Lyria returned no predictions")
    b64 = preds[0].get("bytesBase64Encoded") or preds[0].get("audioContent")
    if not b64:
        raise RuntimeError(f"Lyria prediction had no audio (keys={list(preds[0])})")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(base64.b64decode(b64))
    return out_wav


def to_mp3(wav: Path, mp3: Path) -> Path:
    mp3.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ff(), "-y", "-v", "error", "-i", str(wav), "-codec:a", "libmp3lame",
                    "-q:a", "2", str(mp3)], check=True)
    return mp3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grammar", choices=list(GRAMMAR_PROMPTS))
    ap.add_argument("--prompt", help="free-form prompt (overrides --grammar brief)")
    ap.add_argument("--negative", default=None)
    ap.add_argument("--out", help="explicit output .mp3 (single generation)")
    ap.add_argument("--count", type=int, default=1, help="generate N variants to audition")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--promote", action="store_true",
                    help="write straight to the live assets/bgm/<grammar>_music.mp3")
    args = ap.parse_args()

    if not args.grammar and not args.prompt:
        ap.error("give --grammar or --prompt")
    brief = GRAMMAR_PROMPTS.get(args.grammar or "", {})
    prompt = args.prompt or brief.get("prompt")
    negative = args.negative if args.negative is not None else brief.get("negative", "")

    outs = []
    for i in range(max(1, args.count)):
        seed = args.seed if args.seed is not None else (None if args.count == 1 else i + 1)
        if args.out and args.count == 1:
            mp3 = Path(args.out)
        elif args.promote and args.grammar:
            mp3 = BGM / f"{args.grammar}_music.mp3"
        elif args.grammar:
            mp3 = BGM / (f"{args.grammar}_music.mp3" if args.count == 1
                         else f"{args.grammar}_music_v{i + 1}.mp3")
        else:
            mp3 = Path(args.out or f"/tmp/lyria_{i + 1}.mp3")
        wav = mp3.with_suffix(".wav")
        print(f"[{i+1}/{args.count}] Lyria → {mp3.name}  (seed={seed})", flush=True)
        generate_wav(prompt, wav, negative=negative, seed=seed)
        to_mp3(wav, mp3)
        wav.unlink(missing_ok=True)
        outs.append(str(mp3))
    print("\ndone:")
    for o in outs:
        print(" ", o)
    if args.grammar and not args.promote and args.count > 1:
        print(f"\naudition, then promote the best:\n  cp {outs[0]} "
              f"{BGM / (args.grammar + '_music.mp3')}")


if __name__ == "__main__":
    main()
