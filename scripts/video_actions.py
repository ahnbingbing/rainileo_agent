"""scripts/video_actions.py — temporal ACTION/moment extraction from a clip via Gemini video.

Fills the structural gap our frame-sampled VLM tagging (agents/openai_vision.py: 6-10 evenly
sampled stills → one scene_description) cannot: a 1-2s action like "Leo climbs on Ryani's back"
falls BETWEEN the sampled frames, so it is never described and never searchable. Gemini watches
the WHOLE clip natively and returns action moments WITH start/end timestamps.

Uses Gemini on AI Studio (GOOGLE_API_KEY, the same key regen_vtuber_style / motion_b_vlm use) —
Vertex on this project only has Veo/Lyria, not Gemini publisher models. The clip is uploaded via
the Files API (resumable), we wait for it to finish processing, then ask for the moments. PD
2026-10-26 chose this (Gemini action layer) over TwelveLabs / a frame-VLM swap.

  python3 scripts/video_actions.py data/assets/clips/2026/med_..._icloud_x.mov
  # or, importable:  from video_actions import actions;  moments = actions(local_path)
"""
from __future__ import annotations
import json, os, ssl, sys, time, urllib.request, urllib.error
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

BASE = "https://generativelanguage.googleapis.com"
MODEL = os.getenv("GEMINI_VIDEO_MODEL", "gemini-2.5-flash")   # 2.5-pro for hard cases

PROMPT = """This is a short home video. Two pets:
- Leo = an ORANGE TABBY CAT.
- Ryani = a small BLACK FRENCH BULLDOG with NO tail (white markings on chin, chest, paws).

Watch the whole clip and list every distinct ACTION MOMENT as a JSON array. For each moment:
{"start": <sec>, "end": <sec>, "actor": "leo"|"ryani"|"both", "action": "<short label>", "desc": "<one phrase>"}

Prioritise DYNAMIC actions and say exactly who does what:
- one pet climbing / jumping / pouncing ON the other (e.g. "Leo climbs onto Ryani's back")
- biting or tugging a toy / tug-rope / stick ("Ryani bites the tug toy")
- running, darting, chasing
- hunting / stalking / leaping to a windowsill to catch something
- play-fighting, wrestling, mouthing
- hiding under furniture (table, sofa)
Report who is ALONE in frame vs. both. Ignore plain sitting/sleeping/walking unless nothing else happens.
Timestamps are seconds from the clip start. Return ONLY the JSON array, no prose."""


def _key() -> str:
    k = os.getenv("GOOGLE_API_KEY")
    if not k:
        raise RuntimeError("GOOGLE_API_KEY not set (needed for Gemini video via AI Studio)")
    return k


def _mime(fp: str) -> str:
    return "video/quicktime" if str(fp).lower().endswith(".mov") else "video/mp4"


def _upload(local_path: str) -> str:
    """Resumable Files-API upload → returns the ACTIVE file URI."""
    p = Path(local_path)
    size = p.stat().st_size
    mime = _mime(local_path)
    # 1) start resumable session
    req = urllib.request.Request(
        f"{BASE}/upload/v1beta/files?key={_key()}",
        data=json.dumps({"file": {"display_name": p.name}}).encode(),
        headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60, context=_CTX) as r:
        upload_url = r.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Files API: no upload URL")
    # 2) upload bytes + finalize
    req = urllib.request.Request(
        upload_url, data=p.read_bytes(),
        headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
                 "Content-Length": str(size)}, method="POST")
    with urllib.request.urlopen(req, timeout=300, context=_CTX) as r:
        info = json.loads(r.read())
    f = info["file"]
    name, uri, state = f["name"], f["uri"], f.get("state")
    # 3) wait for video processing to finish (PROCESSING → ACTIVE)
    waited = 0
    while state == "PROCESSING" and waited < 180:
        time.sleep(4); waited += 4
        with urllib.request.urlopen(f"{BASE}/v1beta/{name}?key={_key()}", timeout=40, context=_CTX) as r:
            f = json.loads(r.read()); state = f.get("state"); uri = f.get("uri", uri)
    if state != "ACTIVE":
        raise RuntimeError(f"Files API: file not ACTIVE (state={state})")
    return uri


def actions(local_path: str, *, model: str | None = None) -> list:
    """Upload a local clip to Gemini and return its action moments [{start,end,actor,action,desc}]."""
    uri = _upload(local_path)
    body = json.dumps({
        "contents": [{"parts": [
            {"file_data": {"mime_type": _mime(local_path), "file_uri": uri}},
            {"text": PROMPT}]}],
        # thinkingBudget=0 — 2.5-flash thinking mode truncates JSON mid-stream (CLAUDE.md gotcha #4)
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/v1beta/models/{model or MODEL}:generateContent?key={_key()}",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=240, context=_CTX) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini video HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
    try:
        txt = out["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini video: unexpected response {json.dumps(out)[:300]}")
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        a, b = txt.find("["), txt.rfind("]")
        return json.loads(txt[a:b + 1]) if a != -1 and b != -1 else []


def _upload_as(local_path: str, mime: str) -> str:
    """Files-API upload with an EXPLICIT mime (so audio uploads as audio, not video)."""
    p = Path(local_path); size = p.stat().st_size
    req = urllib.request.Request(
        f"{BASE}/upload/v1beta/files?key={_key()}",
        data=json.dumps({"file": {"display_name": p.name}}).encode(),
        headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60, context=_CTX) as r:
        up = r.headers.get("X-Goog-Upload-URL")
    req = urllib.request.Request(up, data=p.read_bytes(),
        headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
                 "Content-Length": str(size)}, method="POST")
    with urllib.request.urlopen(req, timeout=300, context=_CTX) as r:
        f = json.loads(r.read())["file"]
    name, uri, state = f["name"], f["uri"], f.get("state")
    waited = 0
    while state == "PROCESSING" and waited < 180:
        time.sleep(4); waited += 4
        with urllib.request.urlopen(f"{BASE}/v1beta/{name}?key={_key()}", timeout=40, context=_CTX) as r:
            f = json.loads(r.read()); state = f.get("state"); uri = f.get("uri", uri)
    if state != "ACTIVE":
        raise RuntimeError(f"Files API: {mime} not ACTIVE (state={state})")
    return uri


def align_audio(audio_path: str, lyric_lines: list, *, model: str | None = None) -> list:
    """GROUND-TRUTH karaoke alignment: Gemini listens to the actual song and returns when each
    lyric line is really sung — [{text,start,end}]. Suno's own word-alignment drifts (stretches a
    line, delays the rest), so captions lag the audio; this replaces it. Lines kept in input order."""
    uri = _upload_as(audio_path, "audio/mpeg")
    prompt = ("This is a Korean pop song. Listen and tell me WHEN each lyric line is actually sung. "
              "Return a JSON array [{\"text\":<line>,\"start\":<sec>,\"end\":<sec>}] with precise "
              "seconds from audio start, in this order:\n" + "\n".join(lyric_lines))
    body = json.dumps({
        "contents": [{"parts": [{"file_data": {"mime_type": "audio/mpeg", "file_uri": uri}},
                                 {"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode()
    req = urllib.request.Request(f"{BASE}/v1beta/models/{model or MODEL}:generateContent?key={_key()}",
                                 data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=240, context=_CTX) as r:
        out = json.loads(r.read())
    txt = out["candidates"][0]["content"]["parts"][0]["text"]
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        a, b = txt.find("["), txt.rfind("]")
        return json.loads(txt[a:b + 1]) if a != -1 and b != -1 else []


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: video_actions.py <local_path> [more...]"); return 2
    for fp in sys.argv[1:]:
        try:
            acts = actions(fp)
            print(f"\n=== {os.path.basename(fp)} ===")
            for a in acts:
                print(f"  {a.get('start'):>5}-{str(a.get('end')):<5} [{a.get('actor')}] {a.get('action')} — {a.get('desc')}")
        except Exception as e:
            print(f"ERR {os.path.basename(fp)}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
