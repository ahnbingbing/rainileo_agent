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
import json, os, ssl, subprocess, sys, tempfile, time, urllib.request, urllib.error
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


def _proxy(local_path: str) -> "tuple[str, bool]":
    """Downscale a clip to a small low-res proxy BEFORE the native-video VLM (PD 2026-11-13).
    Action detection doesn't need 1080p — and a full-res cut is the root of the render-time
    timeout: a 1080×1920 cut (~18MB) made generateContent exceed 240s and fail, so the caption
    fell back to the Writer's guessed action ("endures" over a clip where the pet actually kicks).
    A ~480p/12fps proxy (~1.4MB) analyzes in ~20s with the SAME action fidelity. Returns
    (path, is_temp); on any ffmpeg failure returns the original (is_temp=False). Kill-switch
    VIDEO_ACTIONS_PROXY=0."""
    if os.getenv("VIDEO_ACTIONS_PROXY", "1") != "1":
        return local_path, False
    try:
        h = int(os.getenv("VIDEO_ACTIONS_PROXY_H", "854"))
        fps = os.getenv("VIDEO_ACTIONS_PROXY_FPS", "12")
        fd, out = tempfile.mkstemp(suffix="_va_proxy.mp4"); os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-i", local_path,
             "-vf", f"scale=-2:{h}", "-r", str(fps), "-c:v", "libx264", "-crf", "30",
             "-an", out],
            check=True, timeout=120)
        if os.path.getsize(out) > 0:
            return out, True
        os.unlink(out)
    except Exception:
        pass
    return local_path, False


def actions(local_path: str, *, model: str | None = None) -> list:
    """Upload a local clip to Gemini and return its action moments [{start,end,actor,action,desc}].
    The clip is first downscaled to a low-res proxy (_proxy) — a full-res cut times the native VLM
    out; the proxy keeps the action fidelity at a fraction of the payload."""
    proxy, is_temp = _proxy(local_path)
    try:
        return _actions_on(proxy, model=model)
    finally:
        if is_temp:
            try:
                os.unlink(proxy)
            except Exception:
                pass


def _actions_on(local_path: str, *, model: str | None = None) -> list:
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


# clip_knowledge: the richer per-clip understanding the footage-aware RF redesign needs
# (notes/rf_footage_aware_direction_design.md §2.0). Beyond action_moments it adds a condensed
# summary, material-typed beats, AUDIO events (Leo 냐옹 off-frame), and — critically — the
# frame-truth-vs-pd_notes separation (subjects_in_video = who is VISIBLE; pd_verified.conflicts =
# where the owner's note disagrees with the footage). That separation is what structurally stops
# "caption names a pet not on screen" (the 11-21 cross-cut bug).
CK_SCHEMA = (
    '{"summary":"<KO 1~2문장, 이 클립이 보여주는 것>",'
    '"subjects_in_video":["ryani"|"leo"...],'
    '"location":{"indoor_outdoor":"indoor"|"outdoor"|"unknown","specific":"<예: cafe_terrace>"},'
    '"beats":[{"t0":<초>,"t1":<초>,"who":["ryani"|"leo"...],"action":"<짧은 동작>",'
    '"material_type":"mischief|interaction|funny_face|emotion|vocalization|quirk|hook",'
    '"note":"<캡션 소재가 될 한 줄 해석, 없으면 \\"\\">"}],'
    '"audio_events":[{"t0":<초>,"t1":<초>,"who":"ryani"|"leo"|"unknown","sound":"<예: 냐옹/멍/낑낑>",'
    '"type":"vocalization|other","on_screen":true|false}],'
    '"pd_verified":{"subjects_claimed":["..."],"conflicts":["<pd_notes가 영상과 어긋나는 점, 없으면 생략>"],'
    '"context_kept":"<감정·사연 등 영상 밖 맥락 중 살릴 것>"}}'
)


def clip_knowledge(local_path: str, *, pd_notes: str | None = None,
                   captured_iso: str | None = None, model: str | None = None) -> dict:
    """Gemini native video+audio → the clip_knowledge dict (CK_SCHEMA). pd_notes is cross-checked
    against the footage, NOT trusted blindly: a note saying '둘 다' over a clip that shows one pet
    yields subjects_in_video=[the visible one] + pd_verified.conflicts flagging the mismatch."""
    uri = _upload(local_path)
    pd_block = ""
    if pd_notes:
        pd_block = (
            "\n\n[주인(함미하비)이 이 클립에 대해 설명한 내용 — 맹신 금지, 영상으로 교차검증하라]:\n"
            f"\"{pd_notes}\"\n"
            "- 설명이 '둘 다'라 해도 이 클립 프레임엔 한 마리만 보일 수 있다. subjects_in_video 는 "
            "오직 '화면에 실제 보이는' 펫만(소리만 들리는 펫은 audio_events, on_screen=false).\n"
            "- pd_verified.conflicts 에 설명과 영상이 어긋나는 점을 적어라. context_kept 엔 감정·사연 맥락."
        )
    iso_block = f"\n촬영일: {captured_iso} (현재가 아닐 수 있음 — '오늘/지금' 단정 금지)." if captured_iso else ""
    prompt = (
        "This is a short home video of our two pets.\n"
        "- Leo = an ORANGE TABBY CAT.\n"
        "- Ryani = a small BLACK FRENCH BULLDOG, NO tail (white chin/chest/paws).\n\n"
        "영상 전체(화면+소리)를 보고 아래 JSON 하나를 반환하라. summary 는 Director가 훑어 쓸 수 있게 "
        "압축. beats 는 캡션 소재가 될 '흥미로운 순간'을 시간순으로(사소한 앉기/걷기만 있으면 적게). "
        "subjects_in_video 는 화면에 실제 보이는 펫만. 소리로만 있는 펫은 audio_events(on_screen=false). "
        "한국어로 써라." + iso_block + pd_block +
        "\n\nReturn ONLY this JSON object, no prose:\n" + CK_SCHEMA
    )
    body = json.dumps({
        "contents": [{"parts": [
            {"file_data": {"mime_type": _mime(local_path), "file_uri": uri}},
            {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
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
    txt = out["candidates"][0]["content"]["parts"][0]["text"]
    try:
        k = json.loads(txt)
    except json.JSONDecodeError:
        a, b = txt.find("{"), txt.rfind("}")
        if a == -1 or b == -1:
            raise
        k = json.loads(txt[a:b + 1])
    # Stamp the deterministic M0-contract metadata the LLM doesn't own: schema_version (for
    # evolution), captured_iso (temporal-grounding / era-mix read this off the clip, NOT the
    # model's guess), duration_sec (trim-window math). Stamping at the PRODUCER means the backfill
    # bakes complete records once — adding these later would mean re-running Gemini on every clip.
    if isinstance(k, dict):
        k["schema_version"] = CK_SCHEMA_VERSION
        if captured_iso:
            k["captured_iso"] = captured_iso
        k.setdefault("duration_sec", _probe_duration(local_path))
    return k


CK_SCHEMA_VERSION = 1


def _probe_duration(local_path: str) -> "float | None":
    """Best-effort clip duration via ffprobe (None if unavailable — never fatal)."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(local_path)],
            capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip()), 2)
    except Exception:
        return None


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
