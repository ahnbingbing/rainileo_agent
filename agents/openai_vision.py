"""agents/openai_vision.py — pd_notes-authoritative multi-frame grounding VLM.

WHY THIS EXISTS (PD 2026-09-20)
-------------------------------
Captions/titles were betraying the footage: a two-pet cafe-terrace outing got
titled "레오가 나무를 짚었다" (Ryani erased) and an outdoor scene got called "우리 집
일상" (outdoor → home). Root: the generators + the grammar render path grounded on
a SINGLE flash frame, and flash (a) misses the smaller/darker subject on a mid
frame and (b) can't read an ambiguous location (a cafe terrace is neither clearly
indoor nor outdoor — the open sky is only in SOME frames).

Two things fix this and this module provides both:
  1. **Multi-frame span** — sample the whole cut, not one frame, so the subject
     UNION (a pet that only appears in frame 3) and the location (open sky in frame
     5) are recoverable. All frames go in ONE call so the model reasons across them.
  2. **pd_notes as ground truth** — 함미하비 (grandma) writes a human description
     when she sends a clip on Slack; it lands in `assets.pd_notes` and is the single
     most reliable source ("카페에서 레오랑 랴니…밖에 나가서 레오는 나무도 타고" =
     both pets + outdoor, which every VLM got wrong from pixels alone). flash IGNORES
     this override in practice; gpt-4o-mini OBEYS it. So the grounding model is
     `models.VLM_GROUNDING` (gpt-4o-mini), NOT the flash tagger.

Used by:
  - scripts/tag_assets_vlm.py  — the pd_notes re-tag path (bulk, slack+pd_notes assets)
  - agents/cameraman.py        — the render-time per-cut grounding gate (a few clips
                                 an episode actually uses; cheap + output-direct)

This is deliberately model-agnostic on the transport (OpenAI chat vision) so a swap
is one env var (VLM_GROUNDING_MODEL). No DB writes happen here — callers map the
returned dict into their own schema.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger("openai_vision")

try:
    from agents import models as _models
    _GROUNDING_MODEL = _models.VLM_GROUNDING
except Exception:  # pragma: no cover — models canon should always import
    _GROUNDING_MODEL = os.getenv("VLM_GROUNDING_MODEL", "gpt-4o-mini")


# ── Frame sampling across the full span ─────────────────────────────────────
# 5 fractions across the clip. The endpoints are pulled in slightly (0.05/0.95)
# to dodge black lead-in / trailing frames. A cafe terrace's open sky, or a pet
# that only walks into frame late, shows up in exactly these off-center samples —
# a single mid frame (0.50) misses both. Env-tunable for cost/accuracy.
def _fracs() -> list[float]:
    raw = os.getenv("VLM_GROUNDING_FRACS", "0.05,0.30,0.50,0.70,0.95")
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(max(0.0, min(1.0, float(tok))))
        except ValueError:
            continue
    return out or [0.05, 0.30, 0.50, 0.70, 0.95]


def _duration_sec(video_path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(video_path)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return 10.0


def frames_from_video(video_path: Path, n: int | None = None,
                      tmp_prefix: str = "ovground") -> list[Path]:
    """Extract N frames evenly across the clip's span. Returns the JPG paths that
    were successfully written (may be fewer than requested on a very short clip)."""
    video_path = Path(video_path)
    fracs = _fracs()
    if n and n != len(fracs):
        # resample n evenly spaced fractions in (0.05, 0.95)
        if n <= 1:
            fracs = [0.5]
        else:
            fracs = [0.05 + (0.90 * i / (n - 1)) for i in range(n)]
    dur = _duration_sec(video_path)
    tmp = Path(os.getenv("TMPDIR", "/tmp"))
    stem = re.sub(r"[^A-Za-z0-9_]", "", video_path.stem)[-16:] or "clip"
    out: list[Path] = []
    for i, fr in enumerate(fracs):
        p = tmp / f"{tmp_prefix}_{stem}_{i}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(round(dur * fr, 2)), "-i", str(video_path),
                 "-frames:v", "1", "-vf", "scale=768:-1", str(p)],
                capture_output=True, timeout=30)
            if p.exists() and p.stat().st_size > 0:
                out.append(p)
        except Exception:
            continue
    return out


def _resolve_asset_frames(file_path: str, kind: str = "video",
                          n: int | None = None) -> list[Path]:
    """Frames for an asset row. Downloads from GCS if the local copy is missing
    (VM runs the batch; the file may only be in the bucket)."""
    lp: Path
    try:
        from icloud import gcs
        lp = Path(gcs.local_path(file_path))
        if not lp.exists():
            got = gcs.download_to(file_path)
            if got:
                lp = Path(got)
    except Exception:
        lp = Path(file_path)
    if not lp.exists():
        return []
    if kind == "photo":
        return [lp]
    return frames_from_video(lp, n=n)


# ── Prompt ──────────────────────────────────────────────────────────────────
_BASE_PROMPT = """\
You are grounding a real pet video clip for a YouTube Shorts channel with TWO pets:
- **Ryani (랴니)**: small BLACK French Bulldog, NO tail, thin white markings on
  chin/chest/paws, spayed FEMALE, senior (11yo).
- **Leo (레오)**: ORANGE tabby CAT, young (born ~2025-09), male, prankster.

You are shown MULTIPLE frames sampled across the SAME clip's timeline (start→end).
Reason about the WHOLE clip, not any single frame:
- A pet counts as PRESENT if it appears in ANY frame (union across frames). A small
  or dark pet may only be visible in one frame — still count it.
- Judge location from ALL frames together. A cafe TERRACE or a patio is OUTDOOR even
  if one frame looks enclosed — look for open sky, street, trees, railings, exterior.
- Do NOT invent. If the frames genuinely don't show something, say so.

Return ONLY valid JSON (no markdown fences):
{
  "ryani_present": true|false,
  "leo_present": true|false,
  "other_animal": "none" | "cat" | "dog" | "...",
  "indoor_outdoor": "indoor" | "outdoor" | "ambiguous",
  "location_type": "home" | "cafe" | "outdoor" | "vet" | "car" | "other",
  "location_specific": "short phrase, e.g. 'cafe terrace', 'apartment living room', 'park path'",
  "scene_ko": "1-2 factual Korean sentences: who is present (BOTH if both) and where. No embellishment.",
  "confidence": 0.0-1.0,
  "notes": "anything the frames make uncertain (e.g. 'terrace only visible frame 5')"
}
"""


def _pd_notes_clause(pd_notes: str | None) -> str:
    pdn = (pd_notes or "").strip()
    if not pdn:
        return ""
    # Strip pipeline markers, keep the human story.
    for mk in ("[BRANDING]", "[EXCLUDE]"):
        pdn = pdn.replace(mk, "")
    pdn = pdn.strip()
    if not pdn:
        return ""
    return (
        "\n\n★AUTHORITATIVE GROUND TRUTH — the pet owner (grandma) wrote this "
        "description when she sent the clip. It is TRUE and OVERRIDES what any single "
        "frame appears to show. If it says both pets are present, both are present. If "
        "it says they went outside / a cafe / a park, the location is OUTDOOR even if a "
        "frame looks enclosed. Use it to resolve every ambiguity:\n"
        f'  "{pdn}"\n'
        "Reconcile the frames WITH this note; never contradict it.")


def _temporal_clause(captured_iso: str | None) -> str:
    date = (captured_iso or "")[:10]
    if not date:
        return ""
    try:
        from agents import canon
        pre_leo = not canon.pet_exists_on("leo", captured_iso)
    except Exception:
        pre_leo = date < "2025-09-25"
    if pre_leo:
        return (f"\n\n(This clip was captured on {date}. Leo the orange cat did NOT "
                "exist yet — set leo_present=false; any orange cat here is an unknown "
                "cat, not Leo.)")
    return f"\n\n(This clip was captured on {date}.)"


# ── The call ────────────────────────────────────────────────────────────────
def ground_frames(frames: list[Path], pd_notes: str | None = None,
                  captured_iso: str | None = None) -> dict | None:
    """Send all frames in one gpt-4o-mini call with pd_notes as ground truth.
    Returns the parsed dict (see _BASE_PROMPT schema) or None on failure."""
    frames = [Path(f) for f in frames if Path(f).exists()]
    if not frames:
        return None
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        log.warning("openai SDK unavailable: %s", e)
        return None

    prompt = _BASE_PROMPT + _pd_notes_clause(pd_notes) + _temporal_clause(captured_iso)
    content: list[dict] = [{"type": "text", "text": prompt}]
    # Cap frames sent to keep cost bounded (union rarely needs >6).
    max_imgs = int(os.getenv("VLM_GROUNDING_MAX_FRAMES", "6"))
    for f in frames[:max_imgs]:
        b64 = base64.b64encode(f.read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    cl = OpenAI(timeout=int(os.getenv("VLM_GROUNDING_TIMEOUT", "90")), max_retries=2)
    try:
        r = cl.chat.completions.create(
            model=_GROUNDING_MODEL, max_tokens=500,
            messages=[{"role": "user", "content": content}])
    except Exception as e:  # noqa: BLE001
        log.warning("grounding VLM call failed: %s", str(e)[:200])
        return None
    txt = (r.choices[0].message.content or "").strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt)
    try:
        out = json.loads(txt)
    except Exception:
        log.warning("grounding VLM non-JSON: %s", txt[:120])
        return None
    return _normalize(out)


def ground_video(video_path: Path, pd_notes: str | None = None,
                 captured_iso: str | None = None, n: int | None = None) -> dict | None:
    """Convenience: extract frames from a clip on disk, then ground them."""
    frames = frames_from_video(Path(video_path), n=n)
    return ground_frames(frames, pd_notes=pd_notes, captured_iso=captured_iso)


def ground_asset(file_path: str, kind: str = "video", pd_notes: str | None = None,
                 captured_iso: str | None = None, n: int | None = None) -> dict | None:
    """Convenience for a DB asset row: resolve GCS path, extract, ground."""
    frames = _resolve_asset_frames(file_path, kind=kind, n=n)
    return ground_frames(frames, pd_notes=pd_notes, captured_iso=captured_iso)


def _normalize(d: dict) -> dict:
    """Coerce the raw model dict into a stable shape + derive convenience fields."""
    def _b(k):
        v = d.get(k)
        return bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")
    ryani = _b("ryani_present")
    leo = _b("leo_present")
    subjects = [s for s, on in (("ryani", ryani), ("leo", leo)) if on]
    io = str(d.get("indoor_outdoor") or "").strip().lower()
    if io not in ("indoor", "outdoor", "ambiguous"):
        io = "ambiguous"
    loc = str(d.get("location_type") or "").strip().lower()
    if loc not in ("home", "cafe", "outdoor", "vet", "car", "other"):
        loc = "other"
    try:
        conf = float(d.get("confidence"))
    except Exception:
        conf = 0.5
    return {
        "ryani_present": ryani,
        "leo_present": leo,
        "subjects": subjects,                # ['ryani','leo'] union
        "subjects_csv": ",".join(subjects) or None,
        "focus_subject": ("both" if len(subjects) == 2 else (subjects[0] if subjects else "neither")),
        "indoor_outdoor": io,
        "location_type": loc,
        "location_specific": str(d.get("location_specific") or "").strip() or None,
        "scene_ko": str(d.get("scene_ko") or "").strip() or None,
        "other_animal": str(d.get("other_animal") or "none").strip().lower(),
        "confidence": max(0.0, min(1.0, conf)),
        "notes": str(d.get("notes") or "").strip(),
    }
