"""
agents/photo_selector.py — VLM-based Photo Selector.

Searches the asset DB for candidates, sends actual images to Gemini VLM,
and selects the best 8 photos/clips that match the storyboard concept.

Usage:
    from agents.photo_selector import select_photos
    selected = select_photos(concept, n_select=8)

CLI:
    python -m agents.photo_selector --title "랴니 낮잠" --style cartoon_sticker --n 8
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.photo_selector")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

SELECTION_PROMPT = """\
You are the Photo Selector for "Ryani & Leo" YouTube Shorts channel.

Pets:
- **Ryani** (랴니): black French Bulldog, **no tail**, white markings on chin/muzzle, chest, and toes/paws. These markings MUST be visible.
- **Leo** (레오): orange tabby cat, gold-amber eyes, white whiskers.

I'm showing you {n_candidates} candidate photos (numbered 1-{n_candidates}).
Select the best **{n_select}** photos for this concept:

## Concept
{concept_text}

## Selection criteria (STRICT)
1. **Beat matching**: each photo should match a narrative beat (hook, develop, emotion, closer, etc.)
2. **Ryani recognition**: if Ryani is in the photo, her white markings (chin, chest, paws) MUST be clearly visible. Reject dark/shadow photos where she looks like a solid black blob.
3. **Leo recognition**: orange tabby stripes + amber eyes visible. Face must be in frame.
4. **No decoration**: reject photos that already have ANY stickers/filters/text overlays (decoration_level=light or heavy). Only use completely clean/undecorated original photos (decoration_level=none).
5. **Date/location coherence (CRITICAL for real_footage)**:
   - If the concept is a single episode/moment → ALL clips must be from the SAME DATE and SAME LOCATION. Check `captured_iso` dates and `location_type`.
   - If the concept is a "모아보기/compilation" → different dates OK but mention it in the concept.
   - NEVER mix clips from different months into a "single day" concept.
6. **Background consistency**: within the same episode, backgrounds should feel continuous. Don't mix living room + outdoor + cafe in a "daily life at home" concept.
7. **Human minimization**: prefer photos without humans. If human present, only hand/arm is OK.
8. **Quality**: sharp, well-lit, good framing. Reject blurry/dark/overexposed.
9. **Kind match**: for render_style={render_style}, only {kind_required} assets are usable.

## Candidate metadata
{candidate_metadata}

Return JSON:
{{
  "selected": [
    {{
      "photo_number": 1,
      "asset_id": "med_...",
      "beat": "hook",
      "reason": "why this photo fits this beat",
      "caption_ko": "이 사진에 어울리는 캡션"
    }}
  ],
  "rejected_reasons": ["why certain good-looking photos were rejected"],
  "background_variety_check": "어떤 배경들이 선택되었는지"
}}
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def search_candidates(concept: dict, limit: int = 20) -> list[dict]:
    """Search DB for candidate assets matching the concept's requirements."""
    con = _db()
    style = concept.get("render_style", "cartoon_sticker")

    # Determine required kind
    if style == "real_footage":
        kind_filter = "video"
    else:
        kind_filter = "photo"

    # Get subjects
    subjects = concept.get("subjects", ["ryani", "leo"])
    if isinstance(subjects, str):
        subjects = [subjects]

    # Build subject filter
    sub_clauses = " OR ".join(["subjects_csv LIKE ?"] * len(subjects))
    sub_params = [f"%{s}%" for s in subjects]

    if kind_filter == "video":
        # For real_footage: find the best same-date cluster first
        # Step 1: find dates with 3+ clips
        date_rows = con.execute(
            f"""
            SELECT substr(captured_iso, 1, 10) as clip_date, count(*) as cnt
            FROM assets
            WHERE vlm_analyzed_at IS NOT NULL AND kind = 'video'
                  AND quality_score >= 0.7
                  AND (decoration_level IS NULL OR decoration_level = 'none')
                  AND ({sub_clauses})
            GROUP BY clip_date HAVING cnt >= 3
            ORDER BY clip_date DESC
            LIMIT 5
            """,
            sub_params,
        ).fetchall()

        if date_rows:
            # Pick a random good date cluster
            import random
            chosen_date = random.choice(date_rows)["clip_date"]
            # Find the dominant location for that date
            loc_row = con.execute(
                f"""
                SELECT location_type, count(*) as cnt FROM assets
                WHERE kind='video' AND substr(captured_iso, 1, 10) = ?
                      AND location_type IS NOT NULL
                GROUP BY location_type ORDER BY cnt DESC LIMIT 1
                """,
                (chosen_date,),
            ).fetchone()
            loc_filter = loc_row["location_type"] if loc_row else None

            loc_clause = "AND location_type = ?" if loc_filter else ""
            loc_params = [loc_filter] if loc_filter else []

            rows = con.execute(
                f"""
                SELECT asset_id, file_path, kind, scene_description, activity,
                       subjects_csv, has_human, quality_score, mood, background,
                       decoration_level, focus_subject, captured_iso, duration_sec,
                       location_type
                FROM assets
                WHERE vlm_analyzed_at IS NOT NULL AND kind = 'video'
                      AND quality_score >= 0.7
                      AND file_path NOT LIKE '%.heic'
                      AND (decoration_level IS NULL OR decoration_level = 'none')
                      AND ({sub_clauses})
                      AND substr(captured_iso, 1, 10) = ?
                      {loc_clause}
                ORDER BY captured_iso ASC
                LIMIT ?
                """,
                sub_params + [chosen_date] + loc_params + [limit],
            ).fetchall()
        else:
            # Fallback: recent clips
            rows = con.execute(
                f"""
                SELECT asset_id, file_path, kind, scene_description, activity,
                       subjects_csv, has_human, quality_score, mood, background,
                       decoration_level, focus_subject, captured_iso, duration_sec,
                       location_type
                FROM assets
                WHERE vlm_analyzed_at IS NOT NULL AND kind = 'video'
                      AND quality_score >= 0.7
                      AND file_path NOT LIKE '%.heic'
                      AND (decoration_level IS NULL OR decoration_level = 'none')
                      AND ({sub_clauses})
                ORDER BY captured_iso DESC
                LIMIT ?
                """,
                sub_params + [limit],
            ).fetchall()
    else:
        # For ai_vtuber: random high-quality photos
        rows = con.execute(
            f"""
            SELECT asset_id, file_path, kind, scene_description, activity,
                   subjects_csv, has_human, quality_score, mood, background,
                   decoration_level, focus_subject, captured_iso, duration_sec,
                   location_type
            FROM assets
            WHERE vlm_analyzed_at IS NOT NULL
                  AND kind = ?
                  AND quality_score >= 0.7
                  AND file_path NOT LIKE '%.heic'
                  AND (decoration_level IS NULL OR decoration_level = 'none')
                  AND ({sub_clauses})
            ORDER BY has_human ASC, quality_score DESC, RANDOM()
            LIMIT ?
            """,
            [kind_filter] + sub_params + [limit],
        ).fetchall()

    return [dict(r) for r in rows]


def vlm_select(candidates: list[dict], concept: dict,
               n_select: int = 8) -> dict:
    """Send candidate images to Gemini VLM for intelligent selection."""
    import google.generativeai as genai
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(os.getenv("VLM_MODEL", "gemini-2.5-flash"))

    style = concept.get("render_style", "cartoon_sticker")
    kind_required = "video clips" if style == "real_footage" else "photos"

    # Build concept text
    concept_text = f"Title: {concept.get('title', '?')}\n"
    concept_text += f"Style: {style}\n"
    concept_text += f"Tone: {concept.get('tone', 'warm')}\n"
    cuts = concept.get("cuts", [])
    if cuts:
        concept_text += "Storyboard:\n"
        for i, cut in enumerate(cuts, 1):
            concept_text += f"  Cut {i} ({cut.get('beat', '?')}): {cut.get('description', '?')}\n"

    # Build metadata text
    meta_lines = []
    for i, c in enumerate(candidates, 1):
        meta_lines.append(
            f"Photo {i}: {c['asset_id'][:35]} | "
            f"{c.get('activity', '?')} | "
            f"subjects={c.get('subjects_csv', '?')} | "
            f"human={'Y' if c.get('has_human') else 'N'} | "
            f"q={c.get('quality_score', '?')} | "
            f"bg={c.get('background', '?')} | "
            f"deco={c.get('decoration_level', 'none')} | "
            f"scene: {(c.get('scene_description') or '')[:80]}"
        )

    prompt = SELECTION_PROMPT.format(
        n_candidates=len(candidates),
        n_select=n_select,
        concept_text=concept_text,
        render_style=style,
        kind_required=kind_required,
        candidate_metadata="\n".join(meta_lines),
    )

    # Build image parts
    parts: list[Any] = []
    for i, c in enumerate(candidates, 1):
        fp = Path(c["file_path"])
        if not fp.is_absolute():
            fp = ROOT / fp
        if not fp.exists():
            continue

        if c["kind"] == "video":
            # Extract frame for video
            import subprocess
            import tempfile
            tmp = Path(tempfile.mktemp(suffix=".jpg"))
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "1", "-i", str(fp),
                 "-frames:v", "1", "-q:v", "2", str(tmp)],
                capture_output=True, timeout=10,
            )
            if tmp.exists():
                fp = tmp

        try:
            img = Image.open(fp)
            if img.mode != "RGB":
                img = img.convert("RGB")
            max_dim = 256  # small for batch — 10 images at 256px is fast
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=75)
            parts.append({"mime_type": "image/jpeg", "data": buf.getvalue()})
        except Exception as e:
            log.warning("Failed to load image %d (%s): %s", i, c["asset_id"][:20], e)

    parts.append(prompt)

    response = model.generate_content(
        parts,
        generation_config={"response_mime_type": "application/json"},
        request_options={"timeout": 120},
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    result = json.loads(text)

    # Map photo_number back to asset_id
    for sel in result.get("selected", []):
        pnum = sel.get("photo_number", 0)
        if 1 <= pnum <= len(candidates):
            sel["asset_id"] = candidates[pnum - 1]["asset_id"]
            sel["file_path"] = candidates[pnum - 1]["file_path"]
            sel["kind"] = candidates[pnum - 1]["kind"]

    return result


def _llm_text_fallback(prompt: str, max_tokens: int = 2048) -> str:
    """Text generation cascade for photo_selector (PD 2026-06-02: no Anthropic
    for any text). OpenAI GPT-5 → Gemini 2.5 Pro → Anthropic last resort."""
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.warning("photo_selector OpenAI failed: %s → trying Gemini", e)
    try:
        import os as _os
        import google.generativeai as genai
        genai.configure(api_key=_os.environ["GOOGLE_API_KEY"])
        m = genai.GenerativeModel("gemini-2.5-pro")
        resp = m.generate_content(prompt)
        return (resp.text or "").strip()
    except Exception as e:
        log.warning("photo_selector Gemini failed: %s → trying Anthropic last", e)
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-opus-4-7", max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def fast_select(candidates: list[dict], concept: dict, n_select: int = 8) -> dict:
    """Fast selection using text metadata only (no images). PD 2026-06-02:
    routes through _llm_text_fallback (OpenAI → Gemini → Anthropic last)."""
    concept_text = f"Title: {concept.get('title', '?')}\nStyle: {concept.get('render_style', '?')}\n"
    cuts = concept.get("cuts", [])
    if cuts:
        concept_text += "Cuts:\n"
        for i, c in enumerate(cuts, 1):
            concept_text += f"  {i}. ({c.get('beat', '?')}) {c.get('description', '?')}\n"

    meta_lines = []
    for i, c in enumerate(candidates, 1):
        meta_lines.append(
            f"{i}. {c['asset_id']} | {c.get('activity', '?')} | "
            f"sub={c.get('subjects_csv', '?')} | loc={c.get('location_type', '?')} | "
            f"q={c.get('quality_score', '?')} | date={str(c.get('captured_iso', ''))[:10]} | "
            f"scene: {(c.get('scene_description') or '')[:60]}"
        )

    prompt = (
        f"Select the best {n_select} photos for this YouTube Short concept:\n\n"
        f"{concept_text}\n"
        f"Candidates:\n" + "\n".join(meta_lines) + "\n\n"
        f"Rules: same-date clips preferred for real_footage, background variety, "
        f"Ryani white markings visible, no heavy decoration.\n\n"
        f"Return JSON: {{\"selected\": [{{\"photo_number\": N, \"asset_id\": \"...\", "
        f"\"beat\": \"hook/develop/emotion/closer\", \"reason\": \"...\"}}]}}"
    )

    text = _llm_text_fallback(prompt, max_tokens=2048)
    import re as _re
    match = _re.search(r'\{[\s\S]*\}', text)
    if match:
        result = json.loads(match.group(0))
    else:
        result = json.loads(text)

    for sel in result.get("selected", []):
        pnum = sel.get("photo_number", 0)
        if 1 <= pnum <= len(candidates):
            sel["asset_id"] = candidates[pnum - 1]["asset_id"]
            sel["file_path"] = candidates[pnum - 1]["file_path"]
            sel["kind"] = candidates[pnum - 1]["kind"]

    return result


def search_mixed_assets(subjects: list[str], date_window: tuple[str, str] | None = None,
                         space_hint: str | None = None,
                         video_limit: int = 12, photo_limit: int = 12) -> dict:
    """Asset query for real_footage v2 (Phase 2, PD 2026-06-02).

    Returns a dict with both video and photo candidates so the Writer can
    pick the highest-tier source per beat:
        Tier 1 = real video clip (direct ffmpeg trim)
        Tier 2 = real photo → Seedance i2v
        Tier 3 = (handled at cameraman level, no asset needed)

    `subjects` = list like ["ryani", "leo"].
    `date_window` = (iso_from, iso_to) inclusive, or None for any.
    `space_hint` = location_type filter, or None.
    """
    con = _db()
    if isinstance(subjects, str):
        subjects = [subjects]
    sub_clauses = " OR ".join(["subjects_csv LIKE ?"] * len(subjects))
    sub_params = [f"%{s}%" for s in subjects]
    date_clause, date_params = "", []
    if date_window:
        date_clause = " AND captured_iso >= ? AND captured_iso <= ? "
        date_params = list(date_window)
    space_clause, space_params = "", []
    if space_hint:
        space_clause = " AND location_type = ? "
        space_params = [space_hint]

    base_select = (
        "SELECT asset_id, file_path, kind, scene_description, activity, "
        "subjects_csv, captured_iso, duration_sec, location_type, "
        "quality_score, focus_subject "
        "FROM assets WHERE vlm_analyzed_at IS NOT NULL "
        "AND quality_score >= 0.6 "
        "AND file_path NOT LIKE '%.heic' "
        "AND (decoration_level IS NULL OR decoration_level = 'none') "
    )
    video_rows = con.execute(
        base_select + f"AND kind='video' AND ({sub_clauses}) "
        + date_clause + space_clause
        + "ORDER BY captured_iso DESC LIMIT ?",
        sub_params + date_params + space_params + [video_limit],
    ).fetchall()
    photo_rows = con.execute(
        base_select + f"AND kind='photo' AND ({sub_clauses}) "
        + date_clause + space_clause
        + "ORDER BY captured_iso DESC LIMIT ?",
        sub_params + date_params + space_params + [photo_limit],
    ).fetchall()
    return {
        "videos": [dict(r) for r in video_rows],
        "photos": [dict(r) for r in photo_rows],
        "video_count": len(video_rows),
        "photo_count": len(photo_rows),
    }


def select_photos(concept: dict, n_select: int = 8,
                  n_candidates: int = 10) -> list[dict]:
    """Selection pipeline: search → fast select (metadata) with VLM fallback."""
    log.info("Searching candidates for '%s' (%s)...",
             concept.get("title"), concept.get("render_style"))
    candidates = search_candidates(concept, limit=n_candidates)

    if not candidates:
        raise RuntimeError(f"No candidates found for concept '{concept.get('title')}'")

    # Fast mode: metadata-only selection via Claude (reliable, fast, no images)
    log.info("Found %d candidates, fast-selecting via metadata...", len(candidates))
    try:
        result = fast_select(candidates, concept, n_select=n_select)
    except Exception as e:
        log.warning("Fast select failed, falling back to VLM: %s", e)
        result = vlm_select(candidates, concept, n_select=n_select)

    selected = result.get("selected", [])
    log.info("VLM selected %d photos", len(selected))

    return selected


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(name)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="VLM Photo Selector")
    p.add_argument("--title", default="펫 일상", help="concept title")
    p.add_argument("--style", default="cartoon_sticker",
                   choices=["cartoon_sticker", "ai_vtuber", "real_footage"])
    p.add_argument("--n", type=int, default=8, help="number of photos to select")
    p.add_argument("--candidates", type=int, default=20, help="candidate pool size")
    args = p.parse_args()

    concept = {
        "title": args.title,
        "render_style": args.style,
        "subjects": ["ryani", "leo"],
        "cuts": [
            {"beat": "hook", "description": "시선을 끄는 첫 장면"},
            {"beat": "develop", "description": "상황 전개"},
            {"beat": "emotion", "description": "감정 비트"},
            {"beat": "closer", "description": "평화로운 마무리"},
        ],
    }

    selected = select_photos(concept, n_select=args.n, n_candidates=args.candidates)
    for i, s in enumerate(selected, 1):
        print(f"  {i}. {s.get('asset_id', '?')[:35]} | {s.get('beat', '?')} | {s.get('reason', '')[:60]}")
