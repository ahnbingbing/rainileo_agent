"""
agents/cameraman_brain.py — Cameraman Brain (Phase 1b).

LLM-powered decision layer for the Cameraman Agent. Uses Gemini VLM to:
  1. Search & curate assets from the DB (find best photos/videos for a concept)
  2. Decide camera moves per cut (zoom, pan, trim points)
  3. Generate appropriate AI regen prompts and motion prompts
  4. Build complete manifests ready for the rendering pipeline

The Brain takes a concept card and returns a "shot list" — a fully resolved
plan with specific assets, camera moves, captions, and AI generation params.

Usage:
    from agents.cameraman_brain import plan_shots
    shot_list = plan_shots(card_id_prefix, progress_cb=print)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.cameraman_brain")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

ProgressCb = Callable[[str], None] | None

# ──────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _load_card(con: sqlite3.Connection, prefix: str) -> dict:
    row = con.execute(
        "SELECT * FROM cards WHERE card_id LIKE ? || '%' LIMIT 1",
        (prefix,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"No card matching '{prefix}'")
    return dict(row)


def search_assets(con: sqlite3.Connection, *,
                  subjects: list[str] | None = None,
                  kind: str | None = None,
                  date_from: str | None = None,
                  date_to: str | None = None,
                  age_tag: str | None = None,
                  limit: int = 50) -> list[dict]:
    """Search assets DB with flexible filters."""
    clauses = []
    params: list[Any] = []

    if subjects:
        sub_clauses = []
        for s in subjects:
            sub_clauses.append("subjects_csv LIKE ?")
            params.append(f"%{s}%")
        clauses.append(f"({' OR '.join(sub_clauses)})")

    if kind:
        clauses.append("kind = ?")
        params.append(kind)

    if date_from:
        clauses.append("captured_iso >= ?")
        params.append(date_from)

    if date_to:
        clauses.append("captured_iso <= ?")
        params.append(date_to)

    if age_tag:
        clauses.append("age_tag = ?")
        params.append(age_tag)

    where = " AND ".join(clauses) if clauses else "1=1"
    params.append(limit)

    rows = con.execute(
        f"""
        SELECT asset_id, source, kind, file_path, captured_iso,
               duration_sec, width, height, subjects_csv, age_tag, location_tag
        FROM assets
        WHERE {where}
        ORDER BY captured_iso DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────
# Gemini VLM client
# ──────────────────────────────────────────────────────────────────────
def _call_gemini(prompt: str, images: list[Path] | None = None,
                 model: str | None = None,
                 response_json: bool = True) -> dict | str:
    """Call Gemini with text + optional images. Returns parsed JSON or raw text."""
    import google.generativeai as genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    genai.configure(api_key=api_key)
    model_name = model or os.getenv("VLM_MODEL", "gemini-2.5-flash")
    m = genai.GenerativeModel(model_name)

    parts: list[Any] = []

    # Add images
    if images:
        from PIL import Image
        for img_path in images:
            if not img_path.exists():
                continue
            img = Image.open(img_path)
            # Resize large images to save tokens
            max_dim = 1024
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            parts.append({
                "mime_type": "image/jpeg",
                "data": buf.getvalue(),
            })

    parts.append(prompt)

    gen_config = {}
    if response_json:
        gen_config["response_mime_type"] = "application/json"
    # Note: thinking_config removed — not supported in this SDK version

    response = m.generate_content(parts, generation_config=gen_config)
    text = response.text.strip()

    if response_json:
        # Clean any markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    return text


# ──────────────────────────────────────────────────────────────────────
# Asset Curator — find the best assets for a concept card
# ──────────────────────────────────────────────────────────────────────
CURATOR_PROMPT = """\
You are the Cameraman for "Ryani & Leo" YouTube Shorts channel.
Ryani is a black French Bulldog (born 2015-05-05). Leo is an orange tabby cat (born ~2025-09-25).

Given a concept card and a pool of candidate assets (photos/videos), select the best 4-5 assets
for a 20-second YouTube Short. Consider:
- **Narrative arc**: hook (attention-grabbing) → development → emotional beat → closer
- **Visual variety**: different angles, activities, both subjects represented
- **Quality**: clear subjects, good framing, good lighting
- **Relevance**: matches the card's theme, tone, and seasonal context

For each selected asset, specify:
- **role**: "hook", "develop", "emotion", "closer"
- **camera_move**: one of "static", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"
  - Use zoom_in for close-up emotional moments
  - Use pan for wide shots or to reveal a subject
  - Use static when the subject's natural movement is enough
- **trim_start** / **trim_dur**: for video assets, the best 4-second window
- **caption_ko**: one line of Korean narrator caption for this cut
- **caption_en**: English translation
- **ai_notes**: any notes for AI generation (regen style hints, motion prompt)

Return JSON:
{
  "cuts": [
    {
      "asset_id": "...",
      "role": "hook",
      "camera_move": "zoom_in",
      "trim_start": null,
      "trim_dur": null,
      "caption_ko": "...",
      "caption_en": "...",
      "ai_notes": "..."
    }
  ],
  "bgm_mood": "pick ONE that matches THIS episode's energy/era/place — vary across episodes, don't default to one. Menu: gentle_acoustic | warm_acoustic | soft_lofi | gentle_ambient | cozy | lullaby | playful_upbeat | playful_synth | upbeat_cute | fun | whistle | ukulele | chill_documentary | chill | happy | bright | bossa_summer | jazz_quirky | country_folk | piano_sparkle | drama_orchestral | comedy | bouncy | picnic | optimistic | pet_themed | spooky",
  "reasoning": "brief explanation of selection logic (incl. why this bgm_mood fits)"
}
"""


def curate_assets(card: dict, candidate_assets: list[dict],
                  candidate_images: list[Path] | None = None,
                  progress_cb: ProgressCb = None) -> dict:
    """Use VLM to select and sequence the best assets for a concept card."""
    payload = json.loads(card.get("payload_json", "{}"))

    card_context = {
        "theme": card.get("theme"),
        "tone": card.get("tone_primary"),
        "card_type": card.get("card_type"),
        "seasonal": card.get("seasonal_tag"),
        "narrative": payload.get("narrative_oneliner"),
        "caption_burnin": (payload.get("draft") or {}).get("caption_burnin"),
        "render_style": card.get("render_style"),
        "memory_lane": payload.get("memory_lane"),
    }

    # Summarize candidate assets (without images first for token efficiency)
    asset_summaries = []
    for a in candidate_assets[:30]:  # cap at 30 to stay within token limits
        summary = {
            "asset_id": a["asset_id"],
            "kind": a["kind"],
            "subjects": a.get("subjects_csv"),
            "date": a.get("captured_iso", "")[:10],
            "age_tag": a.get("age_tag"),
            "duration_sec": a.get("duration_sec"),
            "dimensions": f"{a.get('width', '?')}x{a.get('height', '?')}",
        }
        asset_summaries.append(summary)

    user_prompt = (
        f"## Concept Card\n{json.dumps(card_context, ensure_ascii=False, indent=2)}\n\n"
        f"## Candidate Assets ({len(asset_summaries)} items)\n"
        f"{json.dumps(asset_summaries, ensure_ascii=False, indent=2)}\n\n"
        "Select 4-5 best assets and create a shot list."
    )

    if progress_cb:
        progress_cb(":mag: Cameraman Brain analyzing assets...")

    # Text-only call first (fast, cheap) for initial selection
    result = _call_gemini(CURATOR_PROMPT + "\n\n" + user_prompt, images=None)

    # If images are provided, do a visual refinement pass
    if candidate_images and len(candidate_images) > 0:
        selected_ids = [c["asset_id"] for c in result.get("cuts", [])]
        selected_images = []
        for a in candidate_assets:
            if a["asset_id"] in selected_ids:
                fp = Path(a["file_path"])
                if not fp.is_absolute():
                    fp = ROOT / fp
                if fp.exists() and a["kind"] == "photo":
                    selected_images.append(fp)

        if selected_images:
            if progress_cb:
                progress_cb(f":eyes: Visual check on {len(selected_images)} selected photos...")
            refine_prompt = (
                "I've selected these photos for the Short. Look at them and refine:\n"
                "- Are the subjects clearly visible? Any blurry ones to swap?\n"
                "- Suggest specific camera_move for each based on composition.\n"
                "- Write better captions that match what you see.\n\n"
                f"Current shot list:\n{json.dumps(result, ensure_ascii=False, indent=2)}\n\n"
                "Return the same JSON format with refinements."
            )
            result = _call_gemini(
                CURATOR_PROMPT + "\n\n" + refine_prompt,
                images=selected_images[:5],
            )

    return result


# ──────────────────────────────────────────────────────────────────────
# Shot List → Manifests converter
# ──────────────────────────────────────────────────────────────────────
def shot_list_to_manifests(shot_list: dict, card: dict, assets_lookup: dict[str, dict],
                           style: str, work_dir: Path) -> dict:
    """Convert a VLM-generated shot list into pipeline-ready manifest files."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cuts = shot_list.get("cuts", [])

    sources = {}
    captions = {}
    cuts_meta = []

    for i, cut in enumerate(cuts, 1):
        asset_id = cut["asset_id"]
        asset = assets_lookup.get(asset_id)
        if not asset:
            log.warning("Asset %s not found in DB, skipping", asset_id)
            continue

        tag = f"cut{i}_{_slugify(cut.get('role', 'cut'))}"
        fp = asset["file_path"]
        if not Path(fp).is_absolute():
            fp = str(ROOT / fp)

        # Sources
        if style == "real_footage":
            sources[tag] = {
                "source": fp,
                "trim_start": float(cut.get("trim_start") or 0.0),
                "trim_dur": float(cut.get("trim_dur") or 4.0),
            }
            # Add pan if specified
            cam = cut.get("camera_move", "static")
            if cam in ("pan_left", "pan_right"):
                sources[tag]["pan"] = "left_to_right" if cam == "pan_right" else "right_to_left"
        else:
            sources[tag] = fp

        # Captions
        ko = cut.get("caption_ko", "")
        en = cut.get("caption_en", "")
        if style == "real_footage":
            captions[tag] = {
                "scenes": [{"start": 0.2, "end": 4.0, "ko": ko, "en": en}]
            }
        else:
            captions[tag] = {"ko": ko, "en": en}

        cuts_meta.append({
            "tag": tag,
            "asset": asset,
            "camera_move": cut.get("camera_move", "static"),
            "ai_notes": cut.get("ai_notes", ""),
        })

    # Write manifests
    sources_path = work_dir / "sources.json"
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    captions_path = work_dir / "captions.json"
    captions_path.write_text(json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "sources": str(sources_path),
        "captions": str(captions_path),
        "cuts": cuts_meta,
        "bgm_mood": shot_list.get("bgm_mood", "warm_acoustic"),
    }

    # Regen prompts for ai_vtuber
    if style == "ai_vtuber":
        tone = card.get("tone_primary", "warm")
        theme = card.get("theme", "")
        regen = {"_base_style": "", "_preserve_subjects": ""}

        for cm in cuts_meta:
            notes = cm.get("ai_notes", "")
            regen[cm["tag"]] = notes if notes else f"Stylized portrait of pet, {theme} theme, {tone} tone"

        regen_path = work_dir / "regen_prompts.json"
        regen_path.write_text(json.dumps(regen, ensure_ascii=False, indent=2), encoding="utf-8")
        result["regen_prompts"] = str(regen_path)

    return result


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return s.strip("_")[:20] or "cut"


# ──────────────────────────────────────────────────────────────────────
# Main entry: plan_shots
# ──────────────────────────────────────────────────────────────────────
def plan_shots(card_id_prefix: str, *,
               progress_cb: ProgressCb = None,
               visual_check: bool = True) -> dict:
    """
    Full brain pipeline: load card → search assets → VLM curation → shot list.
    Returns the shot_list dict (ready to pass to shot_list_to_manifests).
    """
    con = _db()
    card = _load_card(con, card_id_prefix)
    payload = json.loads(card.get("payload_json", "{}"))

    if progress_cb:
        progress_cb(f":brain: Planning shots for `{card['card_id'][:8]}` — {card.get('theme')}")

    # Determine what subjects to search for
    lane = payload.get("memory_lane") or {}
    subjects_needed = lane.get("subjects") or ["ryani", "leo"]
    if isinstance(subjects_needed, str):
        subjects_needed = [subjects_needed]

    # Determine asset kind needed
    render_style = card.get("render_style")
    kind_filter = None
    if render_style == "real_footage":
        kind_filter = "video"

    # Search for candidate assets
    if progress_cb:
        progress_cb(f":file_folder: Searching assets for subjects={subjects_needed}...")

    candidates = search_assets(
        con,
        subjects=subjects_needed,
        kind=kind_filter,
        limit=30,
    )

    if not candidates:
        raise RuntimeError(f"No assets found for subjects={subjects_needed}")

    if progress_cb:
        progress_cb(f":white_check_mark: Found {len(candidates)} candidate assets")

    # VLM curation
    candidate_images = []
    if visual_check:
        for a in candidates[:10]:
            fp = Path(a["file_path"])
            if not fp.is_absolute():
                fp = ROOT / fp
            if fp.exists() and a["kind"] == "photo":
                candidate_images.append(fp)

    shot_list = curate_assets(
        card, candidates,
        candidate_images=candidate_images if visual_check else None,
        progress_cb=progress_cb,
    )

    n_cuts = len(shot_list.get("cuts", []))
    if progress_cb:
        progress_cb(f":clipboard: Shot list ready — {n_cuts} cuts, bgm={shot_list.get('bgm_mood')}")

    return shot_list


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level="INFO")

    p = argparse.ArgumentParser(description="Cameraman Brain — plan shots for a card")
    p.add_argument("card_id", help="card_id prefix")
    p.add_argument("--no-visual", action="store_true", help="skip VLM visual check")
    p.add_argument("--json", action="store_true", help="output raw JSON")
    args = p.parse_args()

    def _print(msg: str) -> None:
        print(msg)

    try:
        result = plan_shots(args.card_id, progress_cb=_print, visual_check=not args.no_visual)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for i, cut in enumerate(result.get("cuts", []), 1):
                print(f"  Cut {i}: {cut['asset_id'][:20]} | {cut.get('role')} | "
                      f"cam={cut.get('camera_move')} | {cut.get('caption_ko')}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
