"""agents/facecheck.py — human-face detection for the no-face HARD RULE (PD 2026-06-08).

Channel HARD RULE: a human FACE must NEVER be visible. The general review VLM
under-reported faces (called a fully-visible bench man "lower body"), and OpenCV
Haar is unusable here (missed the real sunglasses face, false-positived on fur/
foliage). So we use a DEDICATED, strict Gemini face check — one focused yes/no
question makes the model actually look, unlike the holistic review.

Used by:
  - crop step: face_box(frame) → exclude the face from the 9:16 window, and
  - post-render: video_has_face(clip) → deterministic blocker before publish.

Multiple sampled frames go in ONE Gemini call (cheap/fast). Bounded by the same
VLM_TIMEOUT_MS guard as other calls.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("agents.facecheck")


def _client():
    from google import genai as _genai
    from google.genai import types as _types
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None, None
    client = _genai.Client(api_key=api_key, http_options=_types.HttpOptions(
        timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
    return client, _types


def _extract(video_path, at_sec, out) -> bool:
    try:
        subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                        "-ss", f"{at_sec:.2f}", "-i", str(video_path),
                        "-frames:v", "1", str(out)],
                       check=True, capture_output=True, timeout=30)
        return out.exists() and out.stat().st_size > 0
    except Exception:
        return False


_FACE_PROMPT = (
    "You are a content-safety checker for a pet channel. The rule (PD 2026-06-12): "
    "block ONLY when enough of a person's IDENTIFIABLE FACIAL-BIOMETRIC region is "
    "visible to recognize WHO they are — the region face-recognition (Face ID / "
    "FIDO) uses: the EYES together with the NOSE / eye-region geometry. "
    "VIOLATION (any:true) = the eyes (and usually the nose) are visible enough to "
    "identify the person — still counts if partially turned, sunglasses/hat/mask, "
    "blurry, small, or in the background, as long as that eye-nose region shows. "
    "NOT a violation (OK, do NOT flag): only the LOWER face below the nose "
    "(mouth/chin/jaw, eyes hidden); OR only the UPPER head above the eyebrows "
    "(forehead/hair/scalp, eyes hidden); OR no face at all — the identifying "
    "eye-nose data is absent. Pets (cat/dog) are NOT faces. Hands/legs/torso are OK. "
    "A face ON a screen (TV/monitor/phone/tablet) or in a photo/poster/painting does "
    "NOT count — only a REAL person physically in the scene with the eye-nose region "
    "visible. Return ONLY JSON: {\"frames_with_face\":[indices...], "
    "\"any\":true|false, \"note\":\"where the face is, if any\"}."
)


def video_has_face(video_path, interval: float = 1.5, max_frames: int = 24,
                   min_hits: int = 1) -> tuple[bool, int]:
    """Scan the clip at a fixed `interval` (sec) and check EACH frame individually
    (a batch call dilutes attention and misses faces; a brief ~1s face appearance
    also slips a coarse fixed-count grid — verified). With the default
    `min_hits=1` it short-circuits on the FIRST face (most sensitive — the primary
    HARD-RULE gate). PD 2026-06-12: pass `min_hits=2` for a stricter CONFIRMATION
    pass (a single hallucinated face in one frame had false-positived a clean
    episode into a worse re-render) — it requires faces in ≥`min_hits` distinct
    frames before returning True. Returns (face, frames_checked). Error → (False, 0);
    Giri review is a second layer."""
    if not os.environ.get("GOOGLE_API_KEY"):
        return (False, 0)
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video_path)],
            capture_output=True, text=True, timeout=20).stdout.strip() or 0)
    except Exception:
        dur = 0.0
    if dur <= 0:
        return (False, 0)
    interval = float(os.getenv("FACE_SCAN_INTERVAL", str(interval)))
    n = min(max_frames, max(1, int(dur / interval)))
    checked = 0
    hits = 0
    with tempfile.TemporaryDirectory() as td:
        for i in range(n):
            t = dur * (i + 0.5) / n
            fp = Path(td) / f"f{i}.jpg"
            checked += 1
            if _extract(video_path, t, fp) and face_box(fp):
                hits += 1
                log.info("face check: FACE at %.1fs of %s (hit %d/%d)",
                         t, Path(video_path).name, hits, min_hits)
                if hits >= min_hits:
                    return (True, checked)  # enough frames hit — block
    return (False, checked)


def face_box(image_path) -> dict | None:
    """Locate a human face in one frame as fractions (x,y,w,h, 0..1) for crop
    exclusion. None if no face / error."""
    client, types = _client()
    if not client:
        return None
    try:
        data = Path(image_path).read_bytes()
        resp = client.models.generate_content(
            model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
            contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                      ("Locate a REAL human face PHYSICALLY PRESENT in this scene, "
                       "but ONLY when the IDENTIFIABLE FACIAL-BIOMETRIC region — the "
                       "EYES together with the NOSE / eye-region geometry that face-"
                       "recognition (Face ID / FIDO) uses — is visible (PD 2026-06-12). "
                       "Count it even if partially turned, sunglasses/hat/mask, blurry, "
                       "small, or background, AS LONG AS that eye-nose region shows. Do "
                       "NOT return a face when ONLY the lower face below the nose "
                       "(mouth/chin/jaw, eyes hidden) is visible, OR ONLY the upper head "
                       "above the eyebrows (forehead/hair/scalp, eyes hidden) is visible "
                       "— those are not identifiable. Do NOT count a face shown ON a "
                       "screen (TV/monitor/phone/tablet/laptop) or in a photo/poster/"
                       "painting/artwork — only a real person in the room. Return ONLY "
                       "JSON {\"face\":{\"x\":..,\"y\":..,\"w\":..,\"h\":..}} as fractions "
                       "0..1 top-left origin, or {\"face\":null} if none.")],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        return (json.loads((resp.text or "{}").strip()) or {}).get("face")
    except Exception as e:
        log.warning("face_box failed: %s", e)
        return None
