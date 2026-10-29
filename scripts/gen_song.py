"""scripts/gen_song.py — generate a FULL SONG WITH VOCALS from lyrics (Suno), for original MVs.

PD 2026-10-29: the original-MV direction (our own song + Ryani&Leo-story lyrics) needs SUNG
vocals — Lyria (gen_music.py) only makes instrumentals. Suno has NO official public API, so we
go through a third-party gateway (default sunoapi.org / apibox). Bearer-token auth; async:
POST a job → poll record-info by taskId → download the mp3(s). Every song is archived with a
manifest (lyrics/style/model recoverable), mirroring gen_music.py ("만든 음악은 계속 저장").

Setup (PD): get an API key from a Suno gateway (https://sunoapi.org) → put in .env:
    SUNO_API_KEY=...
    # optional: SUNO_API_BASE=https://api.sunoapi.org   SUNO_MODEL=V6

  # custom lyrics (sung), pick a style:
  python3 scripts/gen_song.py --lyrics-file data/tmp/mv/lyrics.txt \
      --style "warm acoustic K-indie, gentle male+female duet, cozy, mid-tempo" \
      --title "랴니와 레오" --out data/tmp/mv/song_ours.mp3
  # or a quick description-only (non-custom) song:
  python3 scripts/gen_song.py --prompt "a warm song about a cat and a tailless dog who are best friends"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

ROOT = Path(__file__).resolve().parent.parent
BGM = ROOT / "assets" / "bgm"
ARCHIVE = BGM / "generated"
MANIFEST = ARCHIVE / "manifest.jsonl"

BASE = os.getenv("SUNO_API_BASE", "https://api.sunoapi.org").rstrip("/")
# Poll generously — Suno usually finishes in 1-3 min; cap so a stuck job can't hang forever.
POLL_EVERY = int(os.getenv("SUNO_POLL_SECONDS", "8"))
POLL_MAX = int(os.getenv("SUNO_POLL_MAX_SECONDS", "480"))


def _key() -> str:
    k = os.getenv("SUNO_API_KEY")
    if not k:
        raise SystemExit("SUNO_API_KEY not set — get one from a Suno gateway (e.g. sunoapi.org) "
                         "and add SUNO_API_KEY=... to .env")
    return k


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=60, context=_CTX)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Suno {method} {path} HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    out = json.loads(resp.read().decode())
    if out.get("code") not in (200, None):
        raise SystemExit(f"Suno {path} error code={out.get('code')} msg={out.get('msg')}")
    return out


def _submit(*, lyrics: str | None, prompt: str | None, style: str | None, title: str | None,
            model: str, instrumental: bool, vocal_gender: str | None) -> str:
    custom = bool(lyrics or style or title)
    body = {
        "customMode": custom,
        "instrumental": instrumental,
        "model": model,
        # we poll record-info instead of hosting a callback; gateways still require the field.
        "callBackUrl": os.getenv("SUNO_CALLBACK_URL", "https://example.com/suno/callback"),
    }
    if custom:
        if title:
            body["title"] = title[:80]
        if lyrics and not instrumental:
            body["lyrics"] = lyrics[:5000]
        if style:
            body["style"] = style[:1000]
        if vocal_gender in ("m", "f"):
            body["vocalGender"] = vocal_gender
    if prompt:
        body["prompt"] = prompt[:5000]
    out = _req("POST", "/api/v1/generate", body)
    task = (out.get("data") or {}).get("taskId")
    if not task:
        raise SystemExit(f"Suno generate: no taskId in response: {out}")
    print(f"  suno task = {task} (model={model}, custom={custom})")
    return task


def _poll(task_id: str) -> list[dict]:
    """Poll record-info until SUCCESS; return the list of generated clips (each has audio_url)."""
    waited = 0
    while waited <= POLL_MAX:
        out = _req("GET", f"/api/v1/generate/record-info?taskId={task_id}")
        data = out.get("data") or {}
        status = (data.get("status") or "").upper()
        if status == "SUCCESS":
            clips = ((data.get("response") or {}).get("data")) or (data.get("response") or {}).get("sunoData") or []
            if clips:
                return clips
            raise SystemExit(f"Suno SUCCESS but no clips: {json.dumps(out)[:400]}")
        if status in ("FAILED", "ERROR", "CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED"):
            raise SystemExit(f"Suno task {task_id} {status}: {data.get('errorMessage') or out.get('msg')}")
        time.sleep(POLL_EVERY)
        waited += POLL_EVERY
        print(f"  …{status or 'PENDING'} ({waited}s)")
    raise SystemExit(f"Suno task {task_id} timed out after {POLL_MAX}s")


def _audio_url(clip: dict) -> str | None:
    return clip.get("audio_url") or clip.get("audioUrl") or clip.get("streamAudioUrl") or clip.get("source_audio_url")


def _download(url: str, out: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180, context=_CTX) as r:
        out.write_bytes(r.read())
    return out


def _archive(mp3: Path, *, lyrics, prompt, style, title, model, task_id) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    keep = ARCHIVE / f"song_{stamp}_{task_id[:8]}.mp3"
    try:
        keep.write_bytes(mp3.read_bytes())
    except Exception:
        keep = mp3
    with MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "file": keep.name, "engine": "suno", "model": model,
                            "task_id": task_id, "title": title, "style": style,
                            "prompt": prompt, "lyrics": lyrics}, ensure_ascii=False) + "\n")
    print(f"  archived → {keep}")


def generate_song(*, out_mp3: Path, lyrics: str | None = None, prompt: str | None = None,
                  style: str | None = None, title: str | None = None,
                  model: str | None = None, instrumental: bool = False,
                  vocal_gender: str | None = None, all_clips: bool = False) -> list[Path]:
    """Generate a song via Suno; write the first clip to out_mp3 (and siblings _2.mp3 … if all_clips).
    Returns the written paths. Importable by the MV pipeline."""
    model = model or os.getenv("SUNO_MODEL", "V6")
    print(f"==> Suno generate — {'instrumental' if instrumental else 'vocal'} ({title or prompt or 'song'})")
    task = _submit(lyrics=lyrics, prompt=prompt, style=style, title=title,
                   model=model, instrumental=instrumental, vocal_gender=vocal_gender)
    clips = _poll(task)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, clip in enumerate(clips if all_clips else clips[:1]):
        url = _audio_url(clip)
        if not url:
            continue
        dest = out_mp3 if i == 0 else out_mp3.with_name(f"{out_mp3.stem}_{i+1}{out_mp3.suffix}")
        _download(url, dest)
        print(f"  → {dest}  ({clip.get('duration','?')}s)")
        written.append(dest)
    if not written:
        raise SystemExit("Suno: no downloadable audio_url in any clip")
    _archive(written[0], lyrics=lyrics, prompt=prompt, style=style, title=title,
             model=model, task_id=task)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a song with vocals via Suno (original MVs)")
    ap.add_argument("--lyrics-file", help="path to a .txt of lyrics (custom mode, sung)")
    ap.add_argument("--lyrics", help="inline lyrics string")
    ap.add_argument("--prompt", help="description-only song (non-custom mode)")
    ap.add_argument("--style", help="style/genre/mood, e.g. 'warm acoustic K-indie duet'")
    ap.add_argument("--title", help="song title (≤80 chars)")
    ap.add_argument("--model", default=None, help="V6 / V6_WILD / V6_MINI (default env SUNO_MODEL or V6)")
    ap.add_argument("--vocal-gender", choices=["m", "f"], default=None)
    ap.add_argument("--instrumental", action="store_true")
    ap.add_argument("--all-clips", action="store_true", help="save both Suno variations")
    ap.add_argument("--out", default="data/tmp/mv/song_ours.mp3")
    args = ap.parse_args()

    lyrics = args.lyrics
    if args.lyrics_file:
        lyrics = Path(args.lyrics_file).read_text(encoding="utf-8")
    if not (lyrics or args.prompt):
        return (print("need --lyrics/--lyrics-file (custom) or --prompt (description)") or 2)
    generate_song(out_mp3=ROOT / args.out if not os.path.isabs(args.out) else Path(args.out),
                  lyrics=lyrics, prompt=args.prompt, style=args.style, title=args.title,
                  model=args.model, instrumental=args.instrumental,
                  vocal_gender=args.vocal_gender, all_clips=args.all_clips)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
