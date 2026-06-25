"""
agents/producer.py — Producer Agent (Phase 2).

Daily pipeline orchestrator:
  18:00  propose 3 mini-storyboard concepts → Slack
  18:00–20:00  wait for PD feedback in thread
  20:00  finalize concepts (apply PD edits or auto-approve)
  20:00  Writer generates 3 concept cards
  20:00–21:00  Cameraman renders 3 videos
  21:00  notify Slack for final review → upload

Run:
    python -m agents.producer                           # default: tomorrow
    python -m agents.producer --date 2026-05-22
    python -m agents.producer --dry-run                 # no Slack, no render
    python -m agents.producer --timeout 60              # 1min PD wait (test)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.producer")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
PROPOSE_PROMPT_PATH = ROOT / "agents" / "prompts" / "producer_propose.md"
KST = ZoneInfo("Asia/Seoul")

ProgressCb = Callable[[str], None] | None

STYLE_EMOJI = {
    "real_footage": "\U0001f4f9",      # 📹
    "cartoon_sticker": "\U0001f3a8",   # 🎨
    "ai_vtuber": "\u2728",             # ✨
}

APPROVE_SIGNALS = {"ㅇㅇ", "ㅇ", "ok", "ㄱㄱ", "좋아", "굿", "ㅎㅎ", "넵", "네"}


# ──────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    # PD 2026-06-12: WAL + busy_timeout so concurrent readers/writers (sync, VLM
    # tagging, launch, status queries) don't fail instantly with "database is locked".
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ──────────────────────────────────────────────────────────────────────
# Pool diversity sampling (PD 2026-06-13)
# ──────────────────────────────────────────────────────────────────────
# WHY: best_videos/best_photos used `ORDER BY captured_iso DESC LIMIT N`, which
# collapsed ~3,700 assets down to a recent-biased slice (videos: 96% 2026 / 64%
# home; photos: top-70 by quality+recency). The 170 outdoor clips and the entire
# 2015–2024 archive (122 clips of baby footage) never reached the Writer, so every
# RF concept converged on "집/실내 휴식" and the Macro Reviewer churned. The fix is
# to surface a DIVERSE slice: stratify across (location × year × activity) and
# round-robin so every cell contributes before any cell repeats, then skip
# vis_phash near-duplicates so visually identical clips don't both make the cut.
def _vphash_of(r) -> str | None:
    try:
        v = r["vis_phash"]
    except (IndexError, KeyError):
        return None
    return v or None


def _diversity_sample(rows: list, k: int, *, loc_col: str, act_col: str,
                      year_col: str = "captured_iso", near_dup: int | None = None) -> list:
    """Pick ≤k rows spread across (loc × year × activity) strata.

    `rows` MUST arrive in within-stratum preference order (e.g. both-pets first,
    flattering composition, quality DESC, recency DESC) — that order is preserved
    inside each stratum. Round-robin over strata gives breadth; vis_phash Hamming
    ≤ near_dup skips visual repeats. Missing vis_phash → never treated as a dup
    (best-effort; coverage grows as clips download)."""
    if k <= 0 or len(rows) <= k:
        return list(rows)
    from collections import OrderedDict
    from agents.visual_hash import hamming as _ham, NEAR_DUP as _NEAR_DUP
    if near_dup is None:
        near_dup = _NEAR_DUP
    strata: "OrderedDict[tuple, list]" = OrderedDict()
    for r in rows:
        key = (str(r[loc_col] or "?"),
               (str(r[year_col] or "?"))[:4],
               str(r[act_col] or "?"))
        strata.setdefault(key, []).append(r)
    queues = list(strata.values())
    picked: list = []
    picked_hashes: list[str] = []

    def _too_close(r) -> bool:
        h = _vphash_of(r)
        if not h:
            return False
        for ph in picked_hashes:
            d = _ham(h, ph)
            if d is not None and d <= near_dup:
                return True
        return False

    progressed = True
    while len(picked) < k and progressed:
        progressed = False
        for q in queues:
            if len(picked) >= k:
                break
            while q:
                r = q.pop(0)
                if _too_close(r):
                    continue  # visual near-dup of something already picked
                picked.append(r)
                h = _vphash_of(r)
                if h:
                    picked_hashes.append(h)
                progressed = True
                break
    # If near-dup skipping starved us below k, backfill from leftovers (ignore dups).
    if len(picked) < k:
        chosen = {id(r) for r in picked}
        for q in queues:
            for r in q:
                if id(r) not in chosen:
                    picked.append(r)
                    if len(picked) >= k:
                        break
            if len(picked) >= k:
                break
    return picked[:k]


# ──────────────────────────────────────────────────────────────────────
# Context gathering (reuse patterns from writer.py)
# ──────────────────────────────────────────────────────────────────────
def _gather_context(con: sqlite3.Connection, target: dt.date) -> dict:
    # Recent tone history
    tones = [dict(r) for r in con.execute(
        "SELECT date, tone_primary, intensity FROM tone_history "
        "WHERE date >= date(?, '-7 days') ORDER BY date DESC",
        (target.isoformat(),),
    ).fetchall()]

    # Recent card themes
    themes = [dict(r) for r in con.execute(
        "SELECT date, theme, render_style, tone_primary FROM cards "
        "WHERE date >= date(?, '-7 days') ORDER BY date DESC",
        (target.isoformat(),),
    ).fetchall()]

    # Milestones for target date
    milestones = [dict(r) for r in con.execute(
        "SELECT * FROM milestones WHERE month=? AND day=?",
        (target.month, target.day),
    ).fetchall()]

    # Best available assets — compact summaries for LLM prompt.
    # PD 2026-06-02: scene_description truncation was 40 chars — too short for
    # Writer to ground actions in actual clip content (caused storyboard
    # hallucination: writer invented "glass table / water bowl / green ball"
    # for cuts that were just "Leo lying on floor"). Bumped to 200 chars.
    # Also: pd_notes (manual override via agents/tools/pd_correct_asset.py)
    # take precedence over VLM scene_description when present. Marked [PD] so
    # Writer knows this is authoritative ground truth.
    def _ground_truth_sc(r) -> str:
        pd_n = (r["pd_notes"] or "").strip() if "pd_notes" in r.keys() else ""
        if pd_n:
            return f"[PD] {pd_n[:280]}"
        return (r["scene_description"] or "")[:280]

    def _extra_vlm(r) -> dict:
        """Pull the richer VLM fields (PD 2026-06-02 prompt rev) out of the
        notes JSON blob. Writer uses these to ground actions in observed
        micro-behavior / intent / looking-at, not invented narrative."""
        try:
            n = json.loads(r["notes"] or "{}")
        except Exception:
            n = {}
        out = {}
        for k in ("pet_intent", "looking_at", "micro_behaviors",
                  "contextual_props", "location_specific", "activity_notes"):
            v = n.get(k)
            if v:
                out[k] = v
        return out

    # PD 2026-06-13: diversity-sample photos too (was recent/quality LIMIT 70, so the
    # 2016 baby-Ryani/Leo archive never surfaced). Stratify across background × year ×
    # activity; vis_phash skips visual repeats.
    _photo_rows = con.execute(
        """
        SELECT asset_id, activity, subjects_csv, mood, background, scene_description, pd_notes,
               notes, captured_iso, location_type, vis_phash
        FROM assets
        WHERE vlm_analyzed_at IS NOT NULL AND kind='photo'
              AND quality_score >= 0.7 AND file_path NOT LIKE '%.heic'
              AND (decoration_level IS NULL OR decoration_level = 'none')
        ORDER BY has_human ASC, quality_score DESC, captured_iso DESC
        """,
    ).fetchall()
    # photos have location_type populated (background is empty for photos); year spread
    # matters most here — 600 of the quality photos are 2016 baby-era.
    _photo_rows = _diversity_sample(_photo_rows, 70, loc_col="location_type",
                                    act_col="activity")
    best_photos = [
        {"id": r["asset_id"], "act": r["activity"] or "", "sub": _ground_subjects(r["subjects_csv"], r["captured_iso"]),
         "mood": r["mood"] or "", "bg": r["background"] or "",
         "sc": _ground_truth_sc(r),
         "date": (r["captured_iso"] or "")[:10],   # for the AV era-floor filter
         **_extra_vlm(r)}
        for r in _photo_rows
    ]

    # Group videos by date for continuity (same-day clips = one episode)
    # PD 2026-06-06: derive two flags the Writer needs to avoid incoherent
    # clip sets: `motion` (low-motion clips read as still photos) and `outing`
    # (harness/leash/cafe = a DIFFERENT place from home — must not be mixed).
    _LOW_MOTION_ACTS = {"sitting", "sleeping", "resting", "lying", "loaf_pose",
                        "watching", "looking", "being_held"}
    def _motion_level(r):
        return "low" if (r["activity"] or "").lower() in _LOW_MOTION_ACTS else "ok"
    def _outing_flag(r):
        sc = (_ground_truth_sc(r) or "")
        cues = ("하네스", "harness", "리쉬", "leash", "목줄", "끈", "카페", "cafe",
                "유리 테이블", "외출")
        return any(c in sc for c in cues)

    def _is_both(sub):
        s = (sub or "").lower()
        return ("ryani" in s and "leo" in s)
    def _tod(captured_iso):
        # PD 2026-06-09: "어둡다"는 루미넌스가 아니라 **찍힌 시간**으로 본다. 늦은 저녁/밤
        # 클립은 런칭 Day2부터("저녁엔 이래요!"), Day1은 낮/노멀 시점 우선.
        try:
            h = int((captured_iso or "")[11:13])
        except (ValueError, IndexError):
            return "?"
        if 7 <= h < 17:
            return "낮"        # daytime / normal
        if 17 <= h < 20:
            return "저녁"      # evening
        return "밤"            # late night / early morning
    # PD 2026-06-13: ensure the perceptual-hash column exists, then DIVERSITY-SAMPLE
    # (not recent-bias LIMIT) so the Writer sees outdoor/cafe/archive footage, not just
    # the last month of home-rest clips. SQL fetches ALL quality clips in preference
    # order (both-pets, flattering framing, quality, recency); _diversity_sample picks
    # 100 spread across location × year × activity + skips vis_phash near-dups.
    try:
        from agents.visual_hash import ensure_column as _vphash_ensure_column
        _vphash_ensure_column(con)
    except Exception as _e:
        log.warning("vis_phash ensure_column skipped: %s", _e)
    _video_rows = con.execute(
        """
        SELECT asset_id, activity, subjects_csv, mood, scene_description, pd_notes,
               duration_sec, captured_iso, location_type, notes, has_human, lighting,
               composition, focus_subject, vis_phash
        FROM assets
        WHERE vlm_analyzed_at IS NOT NULL AND kind='video' AND quality_score >= 0.7
        ORDER BY
          CASE WHEN subjects_csv LIKE '%ryani%' AND subjects_csv LIKE '%leo%' THEN 0 ELSE 1 END,
          CASE WHEN lower(coalesce(composition,'')) IN ('overhead','wide','far') THEN 1 ELSE 0 END,
          quality_score DESC,
          captured_iso DESC
        """,
    ).fetchall()
    _video_rows = _diversity_sample(_video_rows, 100, loc_col="location_type",
                                    act_col="activity")
    best_videos = [
        {"id": r["asset_id"], "act": r["activity"] or "", "sub": _ground_subjects(r["subjects_csv"], r["captured_iso"]),
         "mood": r["mood"] or "", "sc": _ground_truth_sc(r),
         "dur": r["duration_sec"], "date": (r["captured_iso"] or "")[:10],
         "loc": r["location_type"] or "",
         # PD 2026-06-06: surface has_human so the Writer can set crop_out to
         # frame a background person out (instead of letting them appear as an
         # unexplained surprise).
         "has_human": bool(r["has_human"]),
         "motion": _motion_level(r),     # "low" = looks like a still photo
         "outing": _outing_flag(r),      # True = cafe/outing, NOT home
         # PD 2026-06-09: surface togetherness + time-of-day (by capture time, NOT
         # luminance). Launch Day1 = 둘이 같이(`both`) + 낮(`tod`=낮) 우선; 저녁/밤 클립은
         # Day2+("저녁엔 이래요"). Reference ep 20260519_231625 = both together, daytime.
         "both": _is_both(r["subjects_csv"]),
         "tod": _tod(r["captured_iso"]),
         # PD 2026-06-09: surface framing signals so the Writer prefers shots where
         # Leo/Ryani look PRETTY — pet large/clear/engaging — over distant/cluttered
         # ones. comp=medium/close-up + focus=a pet + looking_at=camera = flattering.
         "comp": r["composition"] or "", "focus": r["focus_subject"] or "",
         **_extra_vlm(r)}
        for r in _video_rows
    ]

    # Show date clusters so LLM can pick same-day clips
    date_clusters = {}
    for v in best_videos:
        d = v.get("date", "unknown")
        date_clusters.setdefault(d, []).append(v["id"])
    video_date_summary = {d: len(ids) for d, ids in date_clusters.items() if len(ids) >= 2}

    # ── Archive (memory-lane) clips (PD 2026-06-07, first_month_plan §1b) ──
    # best_videos is the recent-20 only → character-intro / memory-lane episodes
    # had no PAST footage to do "입양 첫날 → 지금" past⇄present narration. Surface
    # a YEAR-STRATIFIED sample of older quality clips (excluding the recent-20),
    # each stamped with years_ago so captions can ground the time point.
    def _years_ago(iso: str) -> float | None:
        try:
            d0 = dt.date.fromisoformat((iso or "")[:10])
            return round((target - d0).days / 365.25, 1)
        except Exception:
            return None
    # also stamp recent clips with years_ago (0 for this year)
    for v in best_videos:
        v["years_ago"] = _years_ago(v.get("date", ""))
    _recent_ids = {v["id"] for v in best_videos}
    ARCHIVE_PER_YEAR = int(os.getenv("ARCHIVE_PER_YEAR", "8"))  # PD 2026-06-11: more old footage in the pool
    archive_videos: list[dict] = []
    _per_year: dict[str, int] = {}
    for r in con.execute(
        """
        SELECT asset_id, activity, subjects_csv, mood, scene_description, pd_notes,
               duration_sec, captured_iso, location_type, notes, has_human
        FROM assets
        WHERE vlm_analyzed_at IS NOT NULL AND kind='video' AND quality_score >= 0.7
          AND captured_iso IS NOT NULL
        ORDER BY quality_score DESC, captured_iso DESC
        """,
    ).fetchall():
        if r["asset_id"] in _recent_ids:
            continue
        yr = (r["captured_iso"] or "")[:4]
        if not yr:
            continue
        if _per_year.get(yr, 0) >= ARCHIVE_PER_YEAR:
            continue
        _per_year[yr] = _per_year.get(yr, 0) + 1
        archive_videos.append({
            "id": r["asset_id"], "act": r["activity"] or "",
            "sub": _ground_subjects(r["subjects_csv"], r["captured_iso"]), "mood": r["mood"] or "",
            "sc": _ground_truth_sc(r), "dur": r["duration_sec"],
            "date": (r["captured_iso"] or "")[:10],
            "years_ago": _years_ago(r["captured_iso"] or ""),
            "loc": r["location_type"] or "", "has_human": bool(r["has_human"]),
            "motion": _motion_level(r), "outing": _outing_flag(r),
            **_extra_vlm(r)})
    archive_videos.sort(key=lambda v: v.get("date", ""))  # oldest → newest
    archive_year_summary = dict(sorted(_per_year.items()))

    # PD 2026-06-11: year-stratified OLD photos too — 2500+ photos exist but only the
    # recent ~50 reached the writer, so aged baby Ryani/Leo (2016~) was never usable
    # (the Ryani-intro needs exactly that). Same year-cap pattern as archive_videos.
    _recent_photo_ids = {p["id"] for p in best_photos}
    archive_photos: list[dict] = []
    _ppy: dict[str, int] = {}
    for r in con.execute(
        """
        SELECT asset_id, activity, subjects_csv, mood, background, scene_description,
               pd_notes, notes, captured_iso, has_human
        FROM assets
        WHERE vlm_analyzed_at IS NOT NULL AND kind='photo' AND quality_score >= 0.7
          AND captured_iso IS NOT NULL AND file_path NOT LIKE '%.heic'
          AND (decoration_level IS NULL OR decoration_level = 'none')
        ORDER BY quality_score DESC, captured_iso DESC
        """,
    ).fetchall():
        if r["asset_id"] in _recent_photo_ids:
            continue
        yr = (r["captured_iso"] or "")[:4]
        if not yr or _ppy.get(yr, 0) >= ARCHIVE_PER_YEAR:
            continue
        _ppy[yr] = _ppy.get(yr, 0) + 1
        archive_photos.append({
            "id": r["asset_id"], "act": r["activity"] or "",
            "sub": _ground_subjects(r["subjects_csv"], r["captured_iso"]), "mood": r["mood"] or "",
            "bg": r["background"] or "", "sc": _ground_truth_sc(r),
            "date": (r["captured_iso"] or "")[:10],
            "years_ago": _years_ago(r["captured_iso"] or ""),
            "has_human": bool(r["has_human"]), **_extra_vlm(r)})
    archive_photos.sort(key=lambda v: v.get("date", ""))

    # Episode stories from #episode channel (least-used first)
    episode_stories = [
        {"text": r["text"], "use_count": r["use_count"]}
        for r in con.execute(
            "SELECT text, use_count FROM episode_stories ORDER BY use_count ASC, created_at DESC LIMIT 10"
        ).fetchall()
    ]

    # Object references from #references channel
    object_refs = []
    try:
        for r in con.execute(
            "SELECT name, description, category, subjects FROM object_refs ORDER BY created_at DESC LIMIT 20"
        ).fetchall():
            object_refs.append({
                "name": r["name"], "description": r["description"],
                "category": r["category"], "subjects": r["subjects"],
            })
    except Exception:
        pass  # table may not exist yet

    # Set library — grouped backgrounds from actual photos
    set_library = {}
    set_lib_path = ROOT / "data" / "set_library.json"
    if set_lib_path.exists():
        try:
            set_library = json.loads(set_lib_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Compact version for LLM context
    bg_refs = []
    for name, info in set_library.items():
        ref = {
            "set_id": name,
            "korean": info.get("korean", ""),
            "props": info.get("props_from_photos", info.get("props", "")),
            "photo_count": info.get("photo_count", 0),
            "window_directions": info.get("window_directions"),
            "window_size": info.get("window_size"),
            "window_note": info.get("window_note"),
        }
        # Phase A/C — pipe auto-synthesized knowledge through to Writer/Director.
        # These fields exist when set_knowledge_builder has been run on this set.
        for k in ("persistent_background", "recurring_items", "typical_actions",
                  "era_changes", "notable_details", "anti_stereotypes",
                  "pd_notes",  # pd_notes = PD-confirmed manual physical facts (highest authority)
                  "room_layout_3d",  # 3D anchor map — Director thinks in 3D first
                  "window_note"):
            if k in info:
                ref[k] = info[k]
        bg_refs.append(ref)

    # background_refs (PD-curated via #background Slack channel — table existed
    # since 2026-05-25 but was NEVER read until this fix on 2026-05-31).
    # Each row has space_name + a Veo-prompt-ready description of one specific
    # photo of one space. Multiple photos can describe the same space.
    try:
        bg_refs_pd = [
            {
                "id": r["id"],
                "space_name": r["space_name"],
                "file_path": r["file_path"],
                "description": r["description"],
            }
            for r in con.execute(
                "SELECT id, space_name, file_path, description FROM background_refs ORDER BY id"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        bg_refs_pd = []

    # set_objects rows (Phase B) — list of all known canonical objects per set.
    # Writer can reference these by name_ko; Director embeds the description in
    # motion_prompts so AI doesn't invent generic props.
    try:
        set_objects = [
            {
                "set_anchor": r["set_anchor"],
                "name_ko": r["name_ko"],
                "description": r["description"],
                "category": r["category"],
                "frequency": r["frequency"],
                "era": r["era"],
                "source": r["source"],
            }
            for r in con.execute(
                "SELECT set_anchor, name_ko, description, category, frequency, era, source "
                "FROM set_objects ORDER BY set_anchor, category, name_ko"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        # Table doesn't exist yet (migration not applied) — graceful fallback
        set_objects = []

    # Phase F: character_library (VLM-learned human cast appearance).
    # Loaded from data/character_library.json + character_objects DB rows.
    char_library: dict = {}
    char_lib_path = ROOT / "data" / "character_library.json"
    if char_lib_path.exists():
        try:
            char_library = json.loads(char_lib_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    character_knowledge = []
    for char_id, info in char_library.items():
        entry = {
            "character_id": char_id,
            "korean": info.get("korean", ""),
            "role": info.get("role", ""),
            "gender": info.get("gender"),
            "age_range": info.get("age_range"),
        }
        for k in ("appearance_summary", "recurring_outfits", "hair",
                  "accessories", "notable_details", "anti_stereotypes",
                  "uncertainty_notes", "pd_notes"):
            if k in info:
                entry[k] = info[k]
        character_knowledge.append(entry)

    try:
        character_objects = [
            {
                "character_id": r["character_id"],
                "name_ko": r["name_ko"],
                "description": r["description"],
                "category": r["category"],
                "frequency": r["frequency"],
                "era": r["era"],
                "source": r["source"],
            }
            for r in con.execute(
                "SELECT character_id, name_ko, description, category, frequency, era, source "
                "FROM character_objects ORDER BY character_id, category, name_ko"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        character_objects = []

    return {
        "target_date": target.isoformat(),
        "tone_history_7d": tones,
        "recent_themes_7d": themes,
        "milestones": milestones,
        "episode_stories": episode_stories,
        "object_references": object_refs,
        "set_library": bg_refs,
        "set_objects": set_objects,
        "pd_background_refs": bg_refs_pd,  # Slack #background PD-curated detailed descriptions
        "character_knowledge": character_knowledge,  # Phase F — VLM-learned human cast appearance
        "character_objects": character_objects,      # Phase G — recurring outfit/hair/accessory rows
        "available_photos": best_photos,
        "available_videos": best_videos,
        "archive_videos": archive_videos,          # PD 2026-06-07: past clips for memory-lane (years_ago stamped)
        "archive_photos": archive_photos,          # PD 2026-06-11: year-stratified OLD photos (baby Ryani/Leo)
        "archive_year_summary": archive_year_summary,
        "video_date_clusters": video_date_summary,
        "video_locations": {r[0]: r[1] for r in con.execute(
            "SELECT location_type, count(*) FROM assets WHERE kind='video' AND vlm_analyzed_at IS NOT NULL GROUP BY location_type"
        ).fetchall()},
    }


# ──────────────────────────────────────────────────────────────────────
# LLM: propose concepts
# ──────────────────────────────────────────────────────────────────────
def _propose_concepts_legacy(target: dt.date, context: dict, style_filter: str | None) -> list[dict]:
    """Single-pass fallback using producer_propose.md (the old behavior)."""
    system = PROPOSE_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = json.dumps(context, ensure_ascii=False, default=str)

    from agents.llm_cascade import call_text_cascade
    user_msg = user_prompt + (
        f"\n\nReturn ONLY a JSON array with EXACTLY 1 concept: {style_filter} only. No prose."
        if style_filter else
        "\n\nReturn ONLY a JSON array with EXACTLY 2 concepts: 1 ai_vtuber + 1 real_footage. No prose."
    )
    system_msg = system + "\n\nIMPORTANT: Output ONLY a JSON array. No explanation, no analysis, no markdown. Just the JSON array starting with [ and ending with ]."
    text = call_text_cascade(system_msg, user_msg, max_tokens=16000).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if not text:
        raise RuntimeError("LLM returned empty response for concept proposals")
    # strict=False tolerates raw control chars (unescaped newlines/tabs inside a
    # JSON string) that LLMs sometimes emit — otherwise a single stray newline in a
    # motion_prompt blew up the whole legacy fallback ("Invalid control character").
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            return json.loads(match.group(0), strict=False)
        raise RuntimeError(f"No valid JSON array in LLM response (len={len(text)}): {text[:200]}")


def _av_era_floor() -> str:
    """Earliest capture date an ai_vtuber present-day concept may source from.

    PD 2026-06-23 (#1 generator grounding). The asset pool is dominated by ~6,600
    pre-Leo (2015-2018) photos/clips; the AV Writer kept pulling them for Leo
    episodes, so the render showed a pre-Leo stray tabby labelled "레오" (ep 034500)
    or cut a 4-month kitten Leo against an 8-month Leo as one moment (택배 v1) — the
    era-mix the deterministic gate then has to reject. Fixing the CHECKER isn't
    enough; the GENERATOR must not select those assets in the first place.

    Floor = Leo's 6-month mark (exists_from + 182d ≈ 2026-03-25): excludes ALL
    pre-Leo footage AND Leo's fast-changing 0-6mo kitten window. With the youngest
    sourced Leo always ≥6 months, the temporal gate's kitten rule can never fire on
    AV, and the >1yr rule can't either — AV concepts pass the era-mix gate by
    construction. Override via env AV_ERA_FLOOR (set "" to disable, e.g. for an
    explicit narrated memory-lane batch that intentionally uses archive footage)."""
    env = os.getenv("AV_ERA_FLOOR")
    if env is not None:
        return env
    try:
        from agents import canon as _canon
        ef = dt.date.fromisoformat(_canon.LEO["exists_from"])
        return (ef + dt.timedelta(days=182)).isoformat()
    except Exception:
        return "2026-03-25"


def propose_concepts(target: dt.date, context: dict, style_filter: str | None = None,
                     progress_cb: ProgressCb = None) -> list[dict]:
    """Generate 1-2 video concepts.

    Default flow (USE_WRITER_DIRECTOR != "0"): Writer (Opus 4.7, 3-pass) → Director
    (Opus 4.7, 1-pass). Story-first, then cinematography.

    Fallback: legacy single-pass using producer_propose.md (Sonnet 4.6).
    Triggers on writer_director failure or when USE_WRITER_DIRECTOR=0.
    """
    # PD 2026-06-05: real_footage uses its DEDICATED concept prompt
    # (realfootage_concept.md) — concept ideation + 쿠들습격 baseline + the
    # 3 quality axes PD demanded (caption wit/말맛, editing rhythm,
    # story depth/twist) + grounding + readability. ONE clean prompt,
    # then _render_realfootage_direct (no card-writer re-dramatization).
    # PD 2026-06-10 (a): reset LLM circuit breakers at the START of each proposal run
    # so a provider that recovered (OpenAI/Gemini back up) is re-probed instead of the
    # whole run staying routed to the last fallback (Anthropic) on stale-open circuits.
    try:
        from agents import circuit as _circuit
        _circuit.reset_all()
    except Exception:
        pass

    # PD 2026-06-13: MACRO Reviewer — fetch the channel macro context (recent episodes
    # + performance + audience comments) ONCE, inject it into the Writer, then after the
    # Writer drafts, review for freshness/audience-fit and rewrite up to N times. This
    # catches "too similar to what we already shipped" that Giri (single-episode) can't.
    from agents import reviewer_macro as _rv
    try:
        _macro = _rv.fetch_macro_context(_db())
        context["macro_context"] = _rv.macro_context_text(_macro)
    except Exception as e:
        log.warning("macro context fetch failed: %s", e)
        _macro = {}

    def _one_pass() -> list:
        if style_filter == "real_footage":
            return _propose_realfootage_singlepass(
                target, context, progress_cb,
                prior_feedback=context.get("reviewer_feedback", "")) or []
        # --- AV path (ai_vtuber) below ---
        # #1 generator grounding (PD 2026-06-23): restrict the asset pools the AV
        # Writer + brainstorm can pick from to the current era, so AV can't source a
        # pre-Leo cat or a kitten-era clip (root of the 034500 / 택배 era-mix). RF
        # already returned above and gets the full pool (memory-lane uses archive).
        # context here is a per-lane copy (launch passes dict(context)), so
        # reassigning the pool keys does not affect the RF call.
        _floor = _av_era_floor()
        if _floor:
            for _key in ("available_photos", "available_videos"):
                _pool = context.get(_key) or []
                _kept = [a for a in _pool if (a.get("date") or "") >= _floor]
                if len(_kept) != len(_pool):
                    log.info("AV era-floor %s: %s %d→%d (dropped %d pre-era)",
                             _floor, _key, len(_pool), len(_kept), len(_pool) - len(_kept))
                context[_key] = _kept
        if os.getenv("USE_WRITER_DIRECTOR", "1") == "0":
            return _propose_concepts_legacy(target, context, style_filter)
        # PD 2026-06-06: feed the UNIFIED arc into the ai_vtuber writer.
        try:
            from agents import arc as _arc
            context["series_so_far"] = _arc.series_so_far(_db(), n=10)
            context["arc_directive"] = _arc.next_directive(
                _db(), today=target.isoformat(), render_style="ai_vtuber")
        except Exception as e:
            log.warning("arc directive (av) failed: %s", e)
        # Same-batch concept-dedup: fold the sibling-slot exclusions INTO the arc
        # directive the Writer/Director always reads — so dedup holds whether or not
        # the (optional, sometimes-off) brainstorm stage runs. [[batch_concept_dedup_gate]]
        try:
            from agents import concept_brainstorm as _cb
            _xb = _cb._exclude_block(context)
            if _xb:
                context["arc_directive"] = (context.get("arc_directive") or "") + "\n" + _xb
        except Exception as e:
            log.warning("av exclude_concepts inject failed: %s", e)
        # PD 2026-06-14: brainstorm storylines and let the reviewer (YouTube-audience lens)
        # pick the winner BEFORE the expensive render — the reviewer gates the IDEA, not the
        # finished $40-50 video. The winning storyline seeds the Writer. CONCEPT_BRAINSTORM=0
        # to disable.
        if os.getenv("CONCEPT_BRAINSTORM", "1") != "0":
            try:
                from agents import concept_brainstorm as _cb
                _brief = (context.get("arc_directive") or "").strip() \
                    or ("레오·랴니의 짧은 숏츠 — 참신한 상상/반사실/일상 과장 중 하나로. "
                        "장소·구조·계절은 이야기에 맞게 자유롭게(거실 현실→상상→현실은 한 옵션일 뿐).")
                _n = int(os.getenv("CONCEPT_BRAINSTORM_N", "5"))
                # Pass context so the brainstorm honors exclude_concepts — same-batch
                # concept-dedup (don't ship two near-identical concepts on one day).
                _res = _cb.best("ai_vtuber", _brief, _n, context=context)
                _win = _res.get("winner")
                if _win:
                    if progress_cb:
                        _rk = " | ".join(f"{c.get('audience_score')}:{c.get('title')}"
                                         for c in _res.get("ranking", [])[:_n])
                        progress_cb(f":brain: 컨셉 {_n}개 브레인스토밍 → 시청자 랭킹: {_rk}")
                        progress_cb(f":trophy: 승자({_win.get('audience_score')}/10): {_win.get('title')}")
                    _beats = _win.get("beats") or []
                    _beat_txt = " / ".join(str(b) for b in _beats) if isinstance(_beats, list) else str(_beats)
                    context["arc_directive"] = (
                        (context.get("arc_directive") or "")
                        + f"\n\n## ★리뷰어가 시청자 관점으로 선택한 스토리라인 (이걸로 전개):\n"
                        + f"제목: {_win.get('title')}\n로그라인: {_win.get('logline')}\n"
                        + f"상상 훅: {_win.get('imagination_hook')}\n비트: {_beat_txt}\n"
                        + "위 스토리라인을 충실히 컷으로 전개하라.")
            except Exception as e:
                log.warning("concept brainstorm gate failed (skipping): %s", e)
        try:
            from agents.writer_director import propose_concepts_v2
            concepts = propose_concepts_v2(
                target, context, style_filter=style_filter, progress_cb=progress_cb) or []
            good = [c for c in concepts if (c.get("cuts") or [])]
            if not good and concepts:
                if progress_cb:
                    progress_cb(":warning: 컨셉에 컷이 없음(LLM 불완전 출력) — 1회 재시도")
                retry = propose_concepts_v2(
                    target, context, style_filter=style_filter, progress_cb=progress_cb) or []
                good = [c for c in retry if (c.get("cuts") or [])]
            if concepts and not good and progress_cb:
                progress_cb(":x: 재시도 후에도 컷 없는 컨셉만 — LLM 불안정, 슬롯 비움(churn 방지)")
            return good
        except Exception as e:
            log.warning("writer_director failed (%s) — falling back to legacy", e)
            if progress_cb:
                progress_cb(f":warning: Writer+Director 실패 ({str(e)[:80]}) — legacy fallback")
            return _propose_concepts_legacy(target, context, style_filter)

    max_rw = int(os.getenv("REVIEWER_MAX_REWRITES", "5"))
    concepts: list = []
    for _attempt in range(max_rw + 1):
        concepts = _one_pass()
        # PD 2026-06-16: enforce the single-wink rule on BOTH generation paths.
        # The legacy fallback doesn't pass through writer_director's caption
        # finalize, so a Director/legacy wink embedded in a story beat + the
        # auto-appended closer would double-wink. _enforce_wink_empty_captions is
        # idempotent, so re-applying it here (after the writer_director path may
        # already have) is safe.
        try:
            from agents.writer_director import (
                _enforce_wink_empty_captions as _wink1,
                _looks_like_wink as _lw, _pick_wink_subject as _pws,
                _build_wink_cut as _bwc)
            for _c in (concepts or []):
                _wink1(_c)  # ≤1 wink: dedupe + strip embedded (safe no-op for RF)
                _cuts = _c.get("cuts") or []
                # …and ≥1 wink, but ONLY for ai_vtuber: the legacy fallback doesn't
                # append a closing wink, so add the canonical closer if none
                # survived. real_footage is REAL clips and must NEVER get an
                # AI-generated wink-ending appended.
                _lane = (_c.get("render_style") or _c.get("style") or style_filter or "")
                if _lane != "real_footage" and _cuts and not any(_lw(cu) for cu in _cuts):
                    _c["cuts"] = _cuts + [_bwc(_pws(_c), _cuts[-1])]
        except Exception as _e:
            log.warning("wink normalize (producer) failed: %s", _e)
        if not concepts:
            break
        verdict = _rv.run_reviewer(concepts, _macro, style_filter or "both", progress_cb)
        if verdict.get("pass") or _attempt >= max_rw:
            break
        context["reviewer_feedback"] = (
            "[Reviewer 거시 피드백 — 최근 업로드와 차별화하라] "
            + (verdict.get("rewrite_directive") or ""))
        if progress_cb:
            progress_cb(f":repeat: Reviewer 재작성 {_attempt + 1}/{max_rw}")
    return concepts


REALFOOTAGE_SINGLEPASS_PROMPT = ROOT / "agents" / "prompts" / "realfootage_concept.md"
# PD 2026-06-13: editing/clip-selection JUDGMENT guide (one-take is occasional, tempo
# /trim/length serve the Writer-Director's original intent, captions match the EDITED
# screen, caption reading-time drives cut length). Injected into agent system prompts so
# the AGENTS decide interactively — NOT hardcoded into producer branches.
EDITING_DIRECTION_PROMPT = ROOT / "agents" / "prompts" / "editing_direction.md"
# Companion PALETTE: the menu of diverse editing techniques the agents choose from
# (one-take / rapid-montage / speed-ramp / day-compilation vlog / twist / slow-mo / …)
# so they don't default to one mode. editing_direction = judgment; techniques = options.
EDITING_TECHNIQUES_PROMPT = ROOT / "agents" / "prompts" / "editing_techniques.md"


def _editing_direction_block() -> str:
    out = ""
    for p in (EDITING_DIRECTION_PROMPT, EDITING_TECHNIQUES_PROMPT):
        try:
            out += "\n\n---\n" + p.read_text(encoding="utf-8")
        except Exception as e:  # never block proposal on a missing guide
            log.warning("editing guide unreadable (%s): %s", p.name, e)
    return out

# PD 2026-06-06: a clip used in a real_footage episode is on COOLDOWN for the
# next N episodes — don't reuse the same footage back-to-back.
RF_CLIP_COOLDOWN_EPISODES = int(os.getenv("RF_CLIP_COOLDOWN_EPISODES", "4"))


def _ensure_uploaded_column(con: sqlite3.Connection) -> None:
    """PD 2026-06-06: cooldown counts only UPLOADED episodes (test renders must
    NOT burn a clip's cooldown). Add an `uploaded` flag the future upload
    pipeline will set to 1. Until then nothing is uploaded → cooldown is inert."""
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
        if "uploaded" not in cols:
            con.execute("ALTER TABLE cards ADD COLUMN uploaded INTEGER DEFAULT 0")
            con.commit()
            log.info("added cards.uploaded column")
    except Exception as e:
        log.warning("ensure uploaded column failed: %s", e)


def _ground_subjects(subjects_csv: "str | None", captured_iso: "str | None") -> str:
    """Strip temporally-impossible pets from a clip's VLM subjects (PD 2026-06-22).

    The VLM tagger labels ANY orange cat 'leo' even in footage that predates his
    ~2025-10 adoption (and any black animal 'ryani'), so old clips carry impossible
    subjects (a 2020 clip tagged 'leo,ryani'). The Writer trusts `sub` verbatim and
    captioned "5년 전 레오". canon.pet_exists_on is the single existence boundary; drop
    any pet that couldn't be in this clip's date so the Writer never names them."""
    if not subjects_csv:
        return ""
    from agents import canon
    kept = [s for s in (x.strip() for x in str(subjects_csv).split(","))
            if s and canon.pet_exists_on(s, captured_iso)]
    return ",".join(kept)


def _recently_used_rf_assets(con: sqlite3.Connection,
                             n: int = RF_CLIP_COOLDOWN_EPISODES) -> set[str]:
    """asset_ids used in the last `n` UPLOADED real_footage episodes (from each
    card's payload_json cuts). PD 2026-06-06: only uploaded=1 counts — test
    renders don't trigger cooldown. RF doesn't populate card_assets → read
    payload_json. (Until the upload pipeline sets uploaded=1, this is empty.)"""
    used: set[str] = set()
    try:
        _ensure_uploaded_column(con)
        rows = con.execute(
            "SELECT payload_json FROM cards WHERE render_style='real_footage' "
            "AND uploaded=1 "
            "ORDER BY created_at DESC LIMIT ?", (n,),
        ).fetchall()
        for r in rows:
            try:
                p = json.loads(r[0] or "{}")
            except Exception:
                continue
            for c in (p.get("cuts") or []):
                aid = c.get("asset_id") or (c.get("asset") or {}).get("asset_id")
                if aid:
                    used.add(aid)
    except Exception as e:
        log.warning("cooldown lookup failed: %s", e)
    # PD 2026-06-12: ALSO cool down clips used in RECENTLY PRODUCED cards regardless
    # of upload — a day of 미게시 test renders kept re-picking the SAME clip.
    # PD 2026-06-15: the 24h `updated_at` window was FRAGILE — a card rendered today
    # but whose updated_at was stamped yesterday (stale) fell off the window edge, so
    # two "랴니 산책" episodes shared clips (the 220044≈072626 dup). FIX: drop the time
    # window (count-based instead) and include 'draft' state (rendered-but-unpinned
    # cards like un-pinned dups). Cool the clips of the last K produced RF cards by
    # created_at — deterministic, no timestamp edge. RF_COOLDOWN_RECENT_CARDS=0 reverts.
    # PD 2026-06-16: the state filter was an ALLOWLIST that silently OMITTED 'approved'
    # — yet 'approved' is where every review-passed-but-not-uploaded RF draft sits (36
    # of them). So the same cafe clips (med_2025_11_21_113536/113742) used by two
    # 'approved' cafe drafts re-appeared in a fresh candidate ("이전거랑 똑같아"). Switch
    # to a DENYLIST: count EVERY produced RF card except explicitly-dead states, so a
    # new intermediate state can never silently fall through the cooldown again
    # (the same "label not rule" failure mode the channel keeps hitting).
    try:
        _k = int(os.getenv("RF_COOLDOWN_RECENT_CARDS", str(max(n, 8) * 3)))
    except Exception:
        _k = max(n, 8) * 3
    if _k > 0:
        try:
            rows2 = con.execute(
                "SELECT payload_json FROM cards WHERE render_style='real_footage' "
                "AND state NOT IN ('discarded','vetoed','failed','rejected') "
                "ORDER BY created_at DESC LIMIT ?",
                (_k,),
            ).fetchall()
            for r in rows2:
                try:
                    p = json.loads(r[0] or "{}")
                except Exception:
                    continue
                for c in (p.get("cuts") or []):
                    aid = c.get("asset_id") or (c.get("asset") or {}).get("asset_id")
                    if aid:
                        used.add(aid)
        except Exception as e:
            log.warning("recent-render cooldown lookup failed: %s", e)
    return used


# PD 2026-06-25: VISUAL cooldown. The exact-id (_recently_used_rf_assets) and
# session (_recently_used_rf_primary_sessions) cooldowns both key on asset_id /
# capture-date — neither knows what a clip LOOKS like. So RF kept shipping
# near-identical footage two ways the id/date keys can't catch: (1) once a clip's
# id-cooldown expires it returns looking identical to a twin still inside the
# window, and (2) a DIFFERENT asset_id from a different day that happens to look
# the same (same couch nap, same window sill) was never cooled at all. PD: "쿨타임
# 지난 동영상 자꾸 비슷하게 뽑는다." Fix: cool any pool clip whose perceptual look
# (vis_phash — the SAME signal _diversity_sample already dedups intra-pool on) is
# within NEAR_DUP Hamming of a clip shipped in the recent RF window. Appearance-
# based, so it catches both leaks. RF_VISUAL_COOLDOWN=0 reverts.
RF_VISUAL_COOLDOWN = os.getenv("RF_VISUAL_COOLDOWN", "1") == "1"


def _recently_used_rf_vphashes(con: sqlite3.Connection,
                               n: int = RF_CLIP_COOLDOWN_EPISODES) -> list[str]:
    """vis_phash signatures of the clips shipped in the recent RF cooldown window
    (same card set as _recently_used_rf_assets). The visual companion to the exact-
    id cooldown: what recent footage LOOKS like, so an appearance-equal clip — a
    different file, or one returning after its id-cooldown lapsed while its twin is
    still in window — is cooled by look, not just id/date."""
    ids = _recently_used_rf_assets(con, n=n)
    if not ids:
        return []
    try:
        from agents.visual_hash import ensure_column as _vph_ensure
        _vph_ensure(con)
        rows = con.execute(
            f"SELECT vis_phash FROM assets WHERE asset_id IN "
            f"({','.join('?' for _ in ids)}) "
            f"AND vis_phash IS NOT NULL AND vis_phash!=''", list(ids)).fetchall()
        return [r[0] for r in rows if r and r[0]]
    except Exception as e:
        log.warning("rf visual-cooldown recent phash lookup failed: %s", e)
        return []


def _rf_visual_cooldown_ids(con: sqlite3.Connection, pool_ids,
                            recent_vphashes: list[str],
                            near_dup: "int | None" = None) -> set[str]:
    """asset_ids in `pool_ids` whose perceptual look is within near_dup Hamming of
    ANY recently-shipped clip's vis_phash = visual near-dups to cool. A missing
    vis_phash is never treated as a dup (best-effort, exactly like _diversity_sample
    — coverage grows as clips download). RF_VISUAL_COOLDOWN_NEARDUP overrides the
    threshold (defaults to visual_hash.NEAR_DUP, the same gap-calibrated value)."""
    if not RF_VISUAL_COOLDOWN or not recent_vphashes:
        return set()
    ids = [i for i in (pool_ids or ()) if i]
    if not ids:
        return set()
    try:
        from agents.visual_hash import hamming as _ham, NEAR_DUP as _ND
    except Exception as e:
        log.warning("rf visual-cooldown import failed: %s", e)
        return set()
    try:
        nd = int(os.getenv("RF_VISUAL_COOLDOWN_NEARDUP", str(_ND)))
    except Exception:
        nd = _ND
    if near_dup is not None:
        nd = near_dup
    cool: set[str] = set()
    try:
        rows = con.execute(
            f"SELECT asset_id, vis_phash FROM assets WHERE asset_id IN "
            f"({','.join('?' for _ in ids)}) "
            f"AND vis_phash IS NOT NULL AND vis_phash!=''", ids).fetchall()
    except Exception as e:
        log.warning("rf visual-cooldown pool phash fetch failed: %s", e)
        return set()
    for r in rows:
        aid, h = r[0], r[1]
        if not aid or not h:
            continue
        for ph in recent_vphashes:
            d = _ham(h, ph)
            if d is not None and d <= nd:
                cool.add(aid)
                break
    return cool


def _robust_json_parse(text: str, allow_llm_repair: bool = True):
    """PD 2026-06-09: parse possibly-malformed LLM JSON robustly. With Gemini
    timing out → Anthropic fallback, the RF singlepass hit 'Expecting , delimiter'
    and the whole slot failed. Steps: strip fences + extract the outermost array/
    object → plain loads → trailing-comma repair → an LLM repair round-trip ('fix
    this into valid JSON'). Raises only if all fail."""
    def _extract(t: str) -> str:
        t = (t or "").strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t[3:]
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        t = t.strip()
        m = re.search(r'\[[\s\S]*\]', t) or re.search(r'\{[\s\S]*\}', t)
        return m.group(0) if m else t

    s = _extract(text)
    try:
        return json.loads(s)
    except Exception:
        pass
    # trailing commas before } or ]
    s2 = re.sub(r',\s*([}\]])', r'\1', s)
    try:
        return json.loads(s2)
    except Exception:
        pass
    if allow_llm_repair:
        try:
            from agents.llm_cascade import call_text_cascade
            fixed = call_text_cascade(
                "You repair malformed JSON. Output ONLY valid JSON — no prose, no "
                "code fences. Keep ALL content; correct only the syntax (quotes, "
                "commas, escapes, newlines inside strings).",
                "Fix this into valid JSON:\n\n" + s,
                max_tokens=8000).strip()
            return json.loads(_extract(fixed))
        except Exception as e:
            log.warning("JSON LLM-repair failed: %s", e)
    raise RuntimeError(f"_robust_json_parse: could not parse/repair (len={len(text or '')})")


def _onetake_today(target: dt.date) -> bool:
    """PD 2026-06-12: single-clip mode (one-take / intra-clip) is an OCCASIONAL editing
    option PER EPISODE, not per-day. The old per-DAY hash made EVERY slot on a 'one-take
    day' single-clip ("왜 또 다 원테이크"). Use a per-episode coin flip (~RF_ONETAKE_RATE,
    default 0.3) so only some episodes are single-clip and most are normal montage."""
    import random as _random
    rate = max(0.0, min(1.0, float(os.getenv("RF_ONETAKE_RATE", "0.3"))))
    return _random.random() < rate


def _should_onetake(target: dt.date, context: dict) -> dict:
    """PD 2026-06-13 (#1): the AGENT decides whether this RF episode is a single
    continuous ONE-TAKE — not a random coin flip ("자꾸 원테이크"). Given the long-clip
    candidates' actual content + the editing JUDGMENT guide (one-take is occasional;
    most RF should be a varied edit), it returns {one_take, clip_id, reason}. Defaults
    to NO unless a clip's continuous moment is itself the story. Fail-safe → NO."""
    longs = _rf_long_candidates(context)
    if not longs:
        return {"one_take": False, "reason": "no long clip available"}
    lines = "\n".join(
        f"- {c.get('id')} | {c.get('dur')}s | {(c.get('sc') or '')[:160]}"
        for c in longs[:8])
    system = (
        "You make ONE editing decision for a 'Ryani & Leo' real-footage YouTube "
        "Short: should this episode be a SINGLE CONTINUOUS ONE-TAKE (one clip, one "
        "unbroken moment played through) — or a normal VARIED edit (montage / several "
        "clips / different techniques)? One-take is an OCCASIONAL choice: pick it ONLY "
        "when ONE clip's continuous moment is ITSELF the whole story — a self-contained "
        "little arc that would lose its magic if cut up. Most episodes should NOT be "
        "one-take; DEFAULT TO false. Judge from the clips' real content below. Return "
        "ONLY JSON: {\"one_take\": true|false, \"clip_id\": \"<id or empty>\", "
        "\"reason\": \"<short>\"}." + _editing_direction_block())
    user = ("Long-clip candidates (longest first):\n" + lines +
            "\n\nDecide one_take true/false. If true, give the clip_id whose continuous "
            "moment best stands alone as the whole story.")
    try:
        from agents.llm_cascade import call_text_cascade
        txt = call_text_cascade(system, user, max_tokens=300).strip()
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
        d = json.loads(txt)
        ot = bool(d.get("one_take"))
        cid = (d.get("clip_id") or "").strip()
        if cid not in {c.get("id") for c in longs}:
            cid = longs[0].get("id")
        log.info("one-take decision: %s (clip=%s) — %s", ot, cid if ot else "-",
                 (d.get("reason") or "")[:140])
        return {"one_take": ot, "clip_id": cid, "reason": d.get("reason", "")}
    except Exception as e:
        log.warning("one-take decision failed (%s) → varied edit", e)
        return {"one_take": False, "reason": "decision error"}


def _recently_used_rf_segments(con: sqlite3.Connection,
                               n: int = RF_CLIP_COOLDOWN_EPISODES,
                               days: int = 0) -> dict:
    """asset_id → list of (start, end) trim windows used by RF episodes. Used to pick a
    DIFFERENT, non-overlapping window when a clip is reused AFTER its whole-clip cooldown
    ("사용한 트림 이외 다른 부분은 또 쓸 수 있어"). `days`>0 widens the lookback to that many
    days (the long history for window-diversity, distinct from the short whole-clip
    cooldown). Unknown duration → a large window (conservative)."""
    segs: dict = {}

    def _add(p):
        for c in (p.get("cuts") or []):
            aid = c.get("asset_id")
            if not aid:
                continue
            try:
                st = float(c.get("trim_start") or 0)
                d = float(c.get("duration_seconds") or c.get("trim_dur") or 0)
            except (TypeError, ValueError):
                st, d = 0.0, 0.0
            segs.setdefault(aid, []).append((st, st + d if d > 0 else st + 1e6))

    try:
        _ensure_uploaded_column(con)
        if days > 0:
            # Long history: every RF card touched in the last `days` days, for window
            # diversity on legitimate (post-cooldown) reuse.
            for r in con.execute(
                "SELECT payload_json FROM cards WHERE render_style='real_footage' "
                "AND (updated_at >= datetime('now', ?) OR created_at >= datetime('now', ?)) "
                "ORDER BY created_at DESC LIMIT 400",
                (f"-{days} days", f"-{days} days")).fetchall():
                try:
                    _add(json.loads(r[0] or "{}"))
                except Exception:
                    pass
            return segs
        for r in con.execute(
            "SELECT payload_json FROM cards WHERE render_style='real_footage' "
            "AND uploaded=1 ORDER BY created_at DESC LIMIT ?", (n,)).fetchall():
            try:
                _add(json.loads(r[0] or "{}"))
            except Exception:
                pass
        try:
            _hrs = int(os.getenv("RF_COOLDOWN_RECENT_HOURS", "24"))
        except Exception:
            _hrs = 24
        if _hrs > 0:
            for r in con.execute(
                "SELECT payload_json FROM cards WHERE render_style='real_footage' "
                "AND state IN ('rendered','published','archived') "
                "AND updated_at >= datetime('now', ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"-{_hrs} hours", max(n, 8) * 3)).fetchall():
                try:
                    _add(json.loads(r[0] or "{}"))
                except Exception:
                    pass
    except Exception as e:
        log.warning("segment cooldown lookup failed: %s", e)
    return segs


def _free_trim_start(clip_dur: float, used_segs: list, win: float,
                     seed: int = 0) -> "float | None":
    """Pick a [start, start+win] window inside [0, clip_dur] that does NOT overlap any
    recently-used segment. Seeded for variety. Returns the start, or None if the clip
    has no free window of length `win` (→ caller should skip the clip)."""
    def _overlaps(a, b):
        return any(a < e and s < b for (s, e) in (used_segs or []))
    slack = max(0.0, clip_dur - win)
    if slack <= 1.0:
        return 0.0 if not _overlaps(0.0, min(win, clip_dur)) else None
    steps = 12
    cands = [round(slack * (i / (steps - 1)), 2) for i in range(steps)]
    off = seed % len(cands)
    for s in cands[off:] + cands[:off]:
        if not _overlaps(s, s + win):
            return s
    return None


def _rf_segment_reuse_overlaps(concept: dict, used_segs: dict,
                               min_overlap: float = 1.0) -> list:
    """Flag cuts whose (asset_id, trim window) overlaps a recently-used segment of the
    SAME clip by >= `min_overlap` seconds → "동일 구간 반복" across episodes. The one-take
    path already avoids this via `_free_trim_start`; this brings the SAME segment-level
    freshness to the normal singlepass path (which previously only had whole-clip
    cooldown — once that expired, any/overlapping trim of a reused clip was allowed).
    Returns human-readable lines for the re-write feedback; empty = clean."""
    out: list = []
    for c in (concept.get("cuts") or []):
        aid = c.get("asset_id")
        wins = used_segs.get(aid)
        if not aid or not wins:
            continue
        try:
            st = float(c.get("trim_start") or 0)
            d = float(c.get("duration_seconds") or c.get("trim_dur") or 0)
        except (TypeError, ValueError):
            continue
        if d <= 0:
            continue
        en = st + d
        for (s, e) in wins:
            ov = min(en, e) - max(st, s)
            if ov >= min_overlap:
                tag = c.get("tag") or c.get("beat") or "cut"
                out.append(
                    f"- {tag}: 클립 {aid}의 {st:.1f}–{en:.1f}s 구간이 최근 사용 구간 "
                    f"{s:.1f}–{e:.1f}s 와 {ov:.1f}s 겹친다 → 겹치지 않는 다른 trim_start로 "
                    f"바꾸거나 다른 클립을 써라(동일 구간 반복 금지).")
                break
    return out


# PD 2026-06-13 (options 2+3): the channel BUMPER itself (assets/branding/intro_bumper.mp4
# = a "Ryani & Leo · cozy moments" collage) also lives in PD's photo library, so it synced
# into the source pool and got picked as a cut → the bumper duplicated mid-episode. Fix:
# exclude ONLY assets PD marked as channel BRANDING (pd_notes contains '[BRANDING]') — a
# generic memory collage stays usable. PD also keeps branding out of the synced album (3).
def _branding_asset_ids(con) -> set:
    try:
        return {r[0] for r in con.execute(
            "SELECT asset_id FROM assets WHERE pd_notes LIKE '%[BRANDING]%'")}
    except Exception as e:
        log.warning("branding asset lookup failed: %s", e)
        return set()


def _rf_location_key(sc: str) -> str:
    """PD 2026-06-13 (#3): a coarse LOCATION signature from a clip's scene_description so
    a PAST clip can be matched to a CURRENT clip at the SAME spot (past↔present bridge).
    Keyword search alone missed it (소파 vs 쿠션 synonyms, photo vs video). Normalize
    seating synonyms + a dominant color so '파란 쿠션'(past) and '파란 소파'(present) group."""
    s = (sc or "").lower()
    if not s:
        return ""
    if any(k in s for k in ("카페", "cafe", "café")):
        place = "cafe"
    elif any(k in s for k in ("소파", "쇼파", "sofa", "쿠션", "cushion", "벤치", "bench", "방석")):
        place = "seating"
    elif any(k in s for k in ("침대", "bed", "이불", "베개")):
        place = "bed"
    elif any(k in s for k in ("창", "window", "창가", "창턱", "선반")):
        place = "window"
    elif any(k in s for k in ("카운터", "counter", "주방", "kitchen", "싱크")):
        place = "kitchen"
    elif any(k in s for k in ("옥상", "rooftop", "현관", "발코니", "베란다", "마당", "산책", "밖")):
        place = "outdoor"
    elif any(k in s for k in ("바닥", "마루", "floor", "러그", "rug")):
        place = "floor"
    else:
        return ""
    color = next((c for c in ("파란", "블루", "blue", "초록", "녹색", "green",
                              "회색", "grey", "gray", "베이지", "흰", "white")
                  if c in s), "")
    return f"{place}:{color}" if color else place


def _rf_location_groups(pool: list) -> dict:
    """Group candidate assets by location_key → {key: [asset_ids]} (only keys with ≥2
    assets = a real same-spot match exists). Injected so the writer can build a visual
    past↔present bridge from the SAME location."""
    groups: dict = {}
    for v in (pool or []):
        if not isinstance(v, dict):
            continue
        k = _rf_location_key(v.get("sc") or "")
        if k and v.get("id"):
            groups.setdefault(k, []).append(v.get("id"))
    return {k: ids for k, ids in groups.items() if len(ids) >= 2}


def _rf_event_clusters(pool: list, min_clips: int = 2, max_outings: int = 12) -> list[dict]:
    """PD 2026-06-17 (RF req #6 — coherent OUTING primitive): group candidate VIDEO
    clips into real events = same capture DATE + same LOCATION. The diversity pool
    scatters same-day clips across (location×year×activity) strata, so the Writer never
    SEES a coherent "one outing" bundle and stitches unrelated clips across years into a
    fake single event ('각자의 방식'). Surfacing the clusters lets the Writer build a
    video-first episode from ONE real outing (PD's own method when hand-picking the
    6/17 cafe day): DIVERSITY BETWEEN outings, COHERENCE WITHIN one.

    Returns the richest outings (most clips → most story material) first, newest as the
    tiebreak. Each outing carries its clip_ids + per-clip scene/activity so the Writer
    can compose 처음→중간→끝 from real footage of that single event."""
    groups: dict = {}
    for v in (pool or []):
        if not isinstance(v, dict):
            continue
        vid = v.get("id")
        date = (v.get("date") or "")[:10]
        if not vid or not date:
            continue
        loc = (v.get("loc") or "unknown") or "unknown"
        groups.setdefault((date, loc), []).append(v)
    outings: list[dict] = []
    for (date, loc), clips in groups.items():
        if len(clips) < min_clips:
            continue
        acts = sorted({c.get("act") for c in clips if c.get("act")})
        ya = next((c.get("years_ago") for c in clips
                   if c.get("years_ago") is not None), None)
        # PD 2026-06-17: rank by EVENT-WORTHINESS, not clip count. Richness ≠ a story.
        # A 3-clip cafe OUTING (arrive→explore→settle) is a better single event than a
        # 13-clip home day of each-pet-doing-its-own-thing (that drifts back to the
        # '각자의 방식' eventless coexistence PD vetoed). A real outing = a trip OUT
        # (cafe/park/outdoor/mom's, the `outing` flag or non-home loc); activity variety
        # within the day signals an arc (탐방+나란히+낮잠) vs a static blob (rest+rest).
        is_outing = any(c.get("outing") for c in clips) or (loc not in ("home", "unknown", ""))
        event_score = ((2 if is_outing else 0)
                       + min(len(acts), 3)
                       + (1 if len(clips) >= 4 else 0))
        outings.append({
            "date": date,
            "location": loc,
            "years_ago": ya,
            "n_clips": len(clips),
            "is_outing": is_outing,
            "activities": acts,
            "clip_ids": [c.get("id") for c in clips],
            "scenes": [(c.get("sc") or "")[:110] for c in clips],
            "_score": event_score,
        })
    # Most event-worthy first (real trips + activity arc), then richer, then newer.
    # Diversity is BETWEEN outings; the Writer picks ONE and stays inside it.
    outings.sort(key=lambda o: (o.get("_score") or 0, o.get("n_clips") or 0,
                                o.get("date") or ""), reverse=True)
    for o in outings:
        o.pop("_score", None)
    return outings[:max_outings]


def _drop_branding(items: list, branding_ids: set) -> list:
    """Remove PD-marked channel branding assets (bumper/promo) from a candidate pool."""
    if not branding_ids:
        return list(items or [])
    out = []
    for v in (items or []):
        vid = v.get("id") if isinstance(v, dict) else v
        if vid in branding_ids:
            log.info("RF pool: dropping branding asset %s", vid)
            continue
        out.append(v)
    return out


def _rf_long_candidates(context: dict) -> list[dict]:
    """The 12s+ original clips available to RF, longest-first (id/dur/sc/date).
    PD 2026-06-12: honor the BATCH dedup (context['exclude_asset_ids'], per slot) so
    two same-day slots don't grab the identical clip+segment. But do NOT apply the
    cross-day upload cooldown here — a long clip has lots of unused footage and CAN be
    reused another day with a DIFFERENT segment ("같은 동영상이더라도 사용하지 않은 구간은
    향후 사용해도 되잖아"); the one-take picks a varied trim_start so re-use differs."""
    _min = float(os.getenv("RF_ONETAKE_MIN_SEC", "12"))
    _win = float(os.getenv("RF_ONETAKE_MAX_SEC", "24"))
    _excl = set(context.get("exclude_asset_ids") or [])  # same-batch dedup = whole clip
    # PD 2026-06-13 (#2 fix): NO back-to-back same clip. A continuous single-action clip's
    # "other window" looks IDENTICAL ("어제 영상에 자막만 바꾼 것"), so the recent cooldown
    # excludes the WHOLE clip. Per-segment reuse applies only AFTER the clip exits cooldown
    # ("향후 다른 구간"): a long history (RF_SEGMENT_HISTORY_DAYS) then picks an UNUSED
    # window so the legitimate re-use isn't the same seconds. RF_ONETAKE_COOLDOWN=0 disables.
    used_segs: dict = {}
    if os.getenv("RF_ONETAKE_COOLDOWN", "1") != "0":
        try:
            _excl |= _recently_used_rf_assets(_db())  # whole-clip: no back-to-back
            used_segs = _recently_used_rf_segments(
                _db(), days=int(os.getenv("RF_SEGMENT_HISTORY_DAYS", "60")))
        except Exception as e:
            log.warning("one-take cooldown lookup failed: %s", e)
    _branding = _branding_asset_ids(_db())
    pool = _drop_branding((context.get("available_videos") or [])
                          + (context.get("archive_videos") or []), _branding)
    # PD 2026-06-25: a one-take's whole clip can also look identical to recent footage
    # without sharing its id — exclude visual near-dups of recently-shipped clips so the
    # single chosen clip isn't a look-alike of yesterday's ("비슷하게"). Same vis_phash
    # signal as the singlepass visual cooldown. RF_VISUAL_COOLDOWN=0 reverts.
    if RF_VISUAL_COOLDOWN:
        try:
            _con = _db()
            _pool_ids = {v.get("id") for v in pool if v.get("id")}
            _excl |= _rf_visual_cooldown_ids(
                _con, _pool_ids, _recently_used_rf_vphashes(_con))
        except Exception as e:
            log.warning("one-take visual cooldown lookup failed: %s", e)
    longs = [{"id": v.get("id"), "dur": float(v.get("dur") or 0),
              "sc": (v.get("sc") or "")[:240], "date": v.get("date")}
             for v in pool
             if isinstance(v.get("dur"), (int, float)) and v.get("dur") >= _min
             and v.get("id") not in _excl]
    longs.sort(key=lambda v: -(v.get("dur") or 0))
    # de-dup by id; attach a free (non-overlapping) trim_start, skip if fully used.
    import hashlib as _hl
    seen, out = set(), []
    for v in longs:
        if not v["id"] or v["id"] in seen:
            continue
        seen.add(v["id"])
        segs = used_segs.get(v["id"], [])
        win = min(v["dur"], _win)
        seed = int(_hl.sha1(v["id"].encode()).hexdigest(), 16)
        ts = _free_trim_start(v["dur"], segs, win, seed)
        if segs and ts is None:
            continue  # every window of this clip was recently used → skip
        v["trim_start"] = ts if ts is not None else 0.0
        v["used_segments"] = segs
        out.append(v)
    return out


def _onetake_time_phrase(date_iso: str | None) -> str:
    """Natural Korean '촬영 시점' phrase for a clip date — '' if recent (<~45d)."""
    if not date_iso:
        return ""
    try:
        d0 = dt.date.fromisoformat(str(date_iso)[:10])
        days = (dt.datetime.now(KST).date() - d0).days
    except Exception:
        return ""
    if days < 45:
        return ""
    if days < 365:
        return f"{max(1, round(days / 30.0))}개월 전"
    return f"{round(days / 365.25)}년 전"


def _vlm_clip_timeline(asset_id: str, win: float, n: int = 6, trim_start: float = 0.0,
                       progress_cb: ProgressCb = None) -> str:
    """PD 2026-06-11: VLM-sample frames ACROSS a clip's [0,win] window and describe
    what UNFOLDS over time (beginning→middle→end), so a one-take's captions match the
    real story (e.g. Leo sneaking the table food while Ryani waits) instead of a static
    one-frame guess. Downloads the clip on demand. Empty string on any failure."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not asset_id:
        return ""
    try:
        from agents.cameraman import _ensure_local
        con = _db()
        row = con.execute("SELECT file_path, source_uuid FROM assets WHERE asset_id=?",
                          (asset_id,)).fetchone()
        if not row:
            return ""
        fp, uuid = row[0], (row[1] if len(row) > 1 else None)
        if fp and not os.path.isabs(fp):
            fp = str(ROOT / fp)
        local = _ensure_local(fp, uuid)
        if not local or not Path(local).exists():
            return ""
        import subprocess as _sp, tempfile as _tf
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        parts = []
        with _tf.TemporaryDirectory() as td:
            for i in range(n):
                t = trim_start + win * (i + 0.5) / n
                jpg = Path(td) / f"t{i}.jpg"
                _sp.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                         "-ss", f"{t:.2f}", "-i", str(local), "-frames:v", "1",
                         str(jpg)], check=False, timeout=20)
                if jpg.exists() and jpg.stat().st_size > 1000:
                    parts.append(_gt.Part.from_bytes(data=jpg.read_bytes(),
                                                     mime_type="image/jpeg"))
            if len(parts) < 2:
                return ""
            parts.append(
                f"These {len(parts)} frames are evenly sampled across one {win:.0f}s "
                "pet clip (in order, earliest first). Describe in 2-4 Korean sentences "
                "what HAPPENS over time — the ACTION and any CHANGE between frames "
                "(who does what, who moves/eats/sneaks/waits), not just a static scene. "
                "고양이=레오, 강아지=랴니. Ground-truth observer; mention only what you see.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=parts,
                config=_gt.GenerateContentConfig(
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            out = (resp.text or "").strip()
            if out and progress_cb:
                progress_cb(f":mag: 원테이크 타임라인 분석: {out[:60]}…")
            return out
    except Exception as e:
        log.warning("one-take VLM timeline failed: %s", e)
        return ""


def _propose_realfootage_onetake(target: dt.date, context: dict,
                                 long_clip: dict, progress_cb: ProgressCb = None,
                                 prior_feedback: str = "") -> list[dict]:
    """PD 2026-06-11: DETERMINISTIC long-original one-take. The montage writer
    ignored every prompt-level instruction to use a long clip whole (still made a
    6-cut montage with a 75s clip sitting in the pool). So when a long clip exists,
    bypass that writer: take ONE long clip as a single cut, and have the LLM write
    only the NARRATOR caption track distributed across its timeline — the STRUCTURE
    (1 clip, one-take) is fixed in code, the LLM only does captions. RF_FORCE_ONETAKE
    gates this; RF_ONETAKE_MAX_SEC trims the window to a Shorts length."""
    from agents.llm_cascade import call_text_cascade
    cid = long_clip.get("id")
    clip_dur = float(long_clip.get("dur") or 0)
    win = min(clip_dur, float(os.getenv("RF_ONETAKE_MAX_SEC", "24")))
    sc = long_clip.get("sc") or ""
    when = long_clip.get("date") or ""
    # PD 2026-06-12: a long clip has lots of footage — DON'T always use the same
    # [0,win] front segment (the 6/13 dup both showed the first 24s). Pick a VARIED
    # window: deterministic offset seeded by date+clip so a re-use shows a DIFFERENT,
    # so-far-unused part of the clip. (Story still completes within `win`.)
    # PD 2026-06-13 (#2): prefer the segment-aware trim_start computed by
    # _rf_long_candidates (avoids recently-used windows). Fall back to a date-seeded
    # offset for variety only if none was attached.
    if long_clip.get("trim_start") is not None:
        trim_start = float(long_clip["trim_start"])
    else:
        import hashlib as _hl
        _slack = max(0.0, clip_dur - win)
        if _slack > 1.0:
            _seed = int(_hl.sha1(f"{target.isoformat()}|{cid}".encode()).hexdigest(), 16)
            trim_start = round((_seed % 1000) / 1000.0 * _slack, 2)
        else:
            trim_start = 0.0
    if progress_cb:
        progress_cb(f":clapper: 긴 원테이크 모드 — {cid[-12:] if cid else '?'} "
                    f"({clip_dur:.0f}s 원본 → {trim_start:.0f}~{trim_start+win:.0f}s 구간)")
    # PD 2026-06-11/12: the static `sc` describes one frame and MISSED the actual story
    # (the cafe clip = Leo secretly eating the table food while Ryani waits). VLM-sample
    # frames across the CHOSEN [trim_start, trim_start+win] window for a beginning→
    # middle→end timeline to ground the captions on what really happens.
    timeline = _vlm_clip_timeline(cid, win, trim_start=trim_start,
                                  progress_cb=progress_cb) or ""
    _ground = (f"TIMELINE (what unfolds over the clip, beginning→end): {timeline}\n"
               if timeline else "") + f"static scene tags: {sc}"
    _time_phrase = _onetake_time_phrase(long_clip.get("date"))
    # PD 2026-06-12: the STORY must finish before the clip ends ("이야기가 채 끝나기도
    # 전에 동영상 종료됨"). Cap captions so they fit the window at a readable pace AND
    # leave a ~1.2s tail (여운) so the last line lands inside the video.
    _cap_win = max(2.6, win - 1.2)
    n_caps = max(3, min(8, int(_cap_win / 2.8)))
    system = (
        "You are the narrator-script writer for the 'Ryani & Leo' pet Shorts "
        "(랴니=11살 암컷 프렌치불독·꼬리 없음, 레오=8개월 수컷 오렌지 고양이). You are given "
        "ONE continuous real clip and must write a casual KOREAN vlog NARRATOR caption "
        "track that rides over it as a SINGLE one-take. CRITICAL: the captions must "
        "follow the TIMELINE of what ACTUALLY happens — the beginning beat, the change "
        "in the middle, the ending — NOT a generic mood. If the timeline says one pet "
        "does something sneaky/funny while the other waits, THAT is the story; tell it. "
        "Build a coherent little arc with a soft ending (여운); add the pets' inner "
        "voice / gentle wit, not flat description. Rules: each caption Korean line + "
        "English line; NO parentheses, no emoji, no speaker labels (랴니:/레오:); never "
        "swap ages/species; "
        + (f"this footage is from {_time_phrase} — you MAY open with that. "
           if _time_phrase else "this footage is recent — do NOT invent a '○년 전'. ")
        + f"distribute exactly {n_caps} captions evenly across the {win:.1f}s timeline "
        "(first starts ~0.2s, ≥2.5s read each, gap-free). Return ONLY JSON: "
        "{\"title\":\"\",\"oneliner\":\"\",\"captions\":[{\"start\":0.2,\"end\":3.0,"
        "\"ko\":\"\",\"en\":\"\"}]}."
        + _editing_direction_block())
    user = (f"clip_id: {cid}\nclip_duration_used: {win:.1f}s\n"
            f"촬영 시점: {_time_phrase or '최근'}\n{_ground}\n"
            + (f"\n[수정 피드백] {prior_feedback}\n" if prior_feedback else "")
            + "\n위 한 클립의 TIMELINE을 따라 흐르는 원테이크 narrator 캡션을 써라.")
    import json as _json
    title, oneliner, caps = "", "", []
    for _ in range(2):
        try:
            txt = call_text_cascade(system, user, max_tokens=1400).strip()
            txt = re.sub(r"^```(?:json)?\s*", "", txt); txt = re.sub(r"\s*```$", "", txt)
            d = _json.loads(txt)
            caps = d.get("captions") or []
            title = (d.get("title") or "").strip()
            oneliner = (d.get("oneliner") or "").strip()
            if caps:
                break
        except Exception as e:
            log.warning("one-take caption gen failed: %s", e)
    if not caps:
        return []
    # PD 2026-06-12: distribute captions within _cap_win (leaving the 여운 tail) so the
    # last line FINISHES before the video ends — never spill past the clip.
    caps = caps[:n_caps]
    n = len(caps)
    for j, s in enumerate(caps):
        s["start"] = round(0.2 if j == 0 else j * _cap_win / n, 2)
        s["end"] = round((j + 1) * _cap_win / n if j < n - 1
                         else max(_cap_win, s["start"] + 1.0), 2)
    concept = {
        "title": title or "랴니와 레오의 한 장면",
        "narrative_oneliner": oneliner or title,
        "render_style": "real_footage",
        "episode_format": "one_take",
        "editing_concept": "long_take",
        "tone": "casual_vlog",
        "subjects": ["ryani", "leo"],
        "duration_target_sec": int(win) + 6,
        "cuts": [{
            "tag": "cut1_onetake",
            "beat": "onetake",
            "who": "both",
            "asset_id": cid,
            "edit_effect": "none",
            "action": sc,
            "duration_seconds": round(win, 1),
            "trim_start": trim_start,
            "captions": caps,
        }],
        "_onetake": True,
    }
    if progress_cb:
        progress_cb(f":white_check_mark: 원테이크 컨셉 — 1컷 {win:.0f}s, 캡션 {len(caps)}개")
    return [concept]


def _vlm_clip_segments(asset_id: str, clip_dur: float, n_frames: int = 9,
                       progress_cb: ProgressCb = None) -> list[dict]:
    """PD 2026-06-12 (intra-clip montage): VLM-scan a LONG clip and pick 2-4 distinct
    INTERESTING segments (start/end seconds + what happens), so we can edit several
    moments FROM ONE clip into a story ("동영상 내 일부 구간들만 모아서 이야기 전개").
    Returns [{start, end, what}] sorted by start; [] on failure / too few."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not asset_id or clip_dur < 8:
        return []
    try:
        from agents.cameraman import _ensure_local
        con = _db()
        row = con.execute("SELECT file_path, source_uuid FROM assets WHERE asset_id=?",
                          (asset_id,)).fetchone()
        if not row:
            return []
        fp, uuid = row[0], (row[1] if len(row) > 1 else None)
        if fp and not os.path.isabs(fp):
            fp = str(ROOT / fp)
        local = _ensure_local(fp, uuid)
        if not local or not Path(local).exists():
            return []
        import subprocess as _sp, tempfile as _tf, json as _json
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        stamps, parts = [], []
        with _tf.TemporaryDirectory() as td:
            for i in range(n_frames):
                t = clip_dur * (i + 0.5) / n_frames
                jpg = Path(td) / f"s{i}.jpg"
                _sp.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-ss",
                         f"{t:.2f}", "-i", str(local), "-frames:v", "1", str(jpg)],
                        check=False, timeout=20)
                if jpg.exists() and jpg.stat().st_size > 1000:
                    stamps.append(round(t, 1))
                    parts.append(_gt.Part.from_bytes(data=jpg.read_bytes(),
                                                     mime_type="image/jpeg"))
            if len(parts) < 3:
                return []
            _maxseg = float(os.getenv("RF_SEGMENT_MAX_SEC", "6"))
            prompt = (
                f"These {len(parts)} frames are sampled from ONE {clip_dur:.0f}s pet "
                f"clip at these timestamps (seconds): {stamps}. 고양이=레오, 강아지=랴니. "
                "Pick the 2-4 MOST interesting, DISTINCT moments to build a short story "
                "(a beginning, a beat, an ending) — each a short segment (≤"
                f"{_maxseg:.0f}s). For each give start/end seconds (within the clip) and "
                "a 1-line Korean description of what happens. Segments must be in time "
                "order and not overlap. Return ONLY JSON: {\"segments\":[{\"start\":0.0,"
                "\"end\":4.0,\"what\":\"\"}]}.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=list(parts) + [prompt],
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            d = _json.loads((resp.text or "{}").strip())
            segs = []
            for s in (d.get("segments") or []):
                try:
                    st = max(0.0, float(s.get("start")))
                    en = min(clip_dur, float(s.get("end")))
                    if en - st >= 1.5:
                        segs.append({"start": round(st, 1),
                                     "end": round(min(en, st + _maxseg), 1),
                                     "what": str(s.get("what", "")).strip()})
                except (TypeError, ValueError):
                    continue
            segs.sort(key=lambda x: x["start"])
            if progress_cb and segs:
                progress_cb(f":scissors: 인트라-클립 구간 {len(segs)}개 발견")
            return segs[:4]
    except Exception as e:
        log.warning("intra-clip segment scan failed: %s", e)
        return []


def _propose_realfootage_intraclip(target: dt.date, context: dict, long_clip: dict,
                                   progress_cb: ProgressCb = None,
                                   prior_feedback: str = "") -> list[dict]:
    """PD 2026-06-12: intra-clip multi-segment montage — collect several moments FROM
    ONE long clip and edit them into a story. A third RF editing option beside one-take
    (single continuous segment) and multi-clip montage. Falls back to [] (→ one-take)
    when fewer than 2 good segments are found."""
    from agents.llm_cascade import call_text_cascade
    cid = long_clip.get("id")
    clip_dur = float(long_clip.get("dur") or 0)
    segs = _vlm_clip_segments(cid, clip_dur, progress_cb=progress_cb)
    if len(segs) < 2:
        return []
    _time_phrase = _onetake_time_phrase(long_clip.get("date"))
    seg_lines = "\n".join(f"  segment {i+1} [{s['start']}~{s['end']}s]: {s['what']}"
                          for i, s in enumerate(segs))
    system = (
        "You write the casual KOREAN vlog NARRATOR captions for a 'Ryani & Leo' Short "
        "(랴니=11살 암컷 프렌치불독·꼬리 없음, 레오=8개월 수컷 오렌지 고양이) that is edited "
        "from SEVERAL moments of ONE clip. Given the ordered segments below, write ONE "
        "caption per segment that together tell a coherent little story with a soft "
        "ending (여운) — follow what each segment ACTUALLY shows, add the pets' inner "
        "voice/wit. Each: Korean line + English line; NO parentheses, no emoji, no "
        "speaker labels; never swap ages/species; "
        + (f"footage from {_time_phrase} — may open with it. " if _time_phrase
           else "recent footage — no '○년 전'. ")
        + "Return ONLY JSON: {\"title\":\"\",\"oneliner\":\"\",\"captions\":[\"ko||en\","
        "...]} with EXACTLY one entry per segment, in order.")
    user = (f"clip_id: {cid}\n촬영 시점: {_time_phrase or '최근'}\nsegments:\n{seg_lines}\n"
            + (f"\n[수정 피드백] {prior_feedback}\n" if prior_feedback else "")
            + "\n각 구간에 1개씩, 순서대로 이어지는 narrator 캡션을 써라.")
    import json as _json
    title, oneliner, lines = "", "", []
    for _ in range(2):
        try:
            txt = call_text_cascade(system, user, max_tokens=1200).strip()
            txt = re.sub(r"^```(?:json)?\s*", "", txt); txt = re.sub(r"\s*```$", "", txt)
            d = _json.loads(txt)
            lines = d.get("captions") or []
            title = (d.get("title") or "").strip()
            oneliner = (d.get("oneliner") or "").strip()
            if lines:
                break
        except Exception as e:
            log.warning("intra-clip caption gen failed: %s", e)
    if not lines:
        return []
    cuts = []
    for i, s in enumerate(segs):
        dur = round(max(1.5, s["end"] - s["start"]), 1)
        ko, en = "", ""
        if i < len(lines) and isinstance(lines[i], str) and "||" in lines[i]:
            ko, en = (p.strip() for p in lines[i].split("||", 1))
        elif i < len(lines):
            ko = str(lines[i]).strip()
        cuts.append({
            "tag": f"cut{i+1}_seg",
            "beat": ("intro" if i == 0 else "closer" if i == len(segs) - 1 else "develop"),
            "who": "both",
            "asset_id": cid,
            "edit_effect": "none",
            "action": s["what"],
            "duration_seconds": dur,
            "trim_start": s["start"],
            "captions": [{"start": 0.1, "end": max(dur - 0.1, 1.0), "ko": ko, "en": en}],
        })
    concept = {
        "title": title or "랴니와 레오의 순간들",
        "narrative_oneliner": oneliner or title,
        "render_style": "real_footage",
        "episode_format": "intra_clip_montage",
        "editing_concept": "intra_clip_montage",
        "tone": "casual_vlog",
        "subjects": ["ryani", "leo"],
        "duration_target_sec": int(sum(c["duration_seconds"] for c in cuts)) + 6,
        "cuts": cuts,
        "_intraclip": True,
    }
    if progress_cb:
        progress_cb(f":white_check_mark: 인트라-클립 몬타주 — {len(cuts)}구간 "
                    f"(한 클립에서 추출)")
    return [concept]


def _recent_la_usage(con, days: int = 7):
    """PD 2026-06-13 (writer-side): how heavily each (location_type, activity) and each
    location has been used by recent real_footage uploads. Returns
    (freq Counter[(loc,act)], overused set[(loc,act)], overused_loc set[loc]). Drives
    BOTH the pool reorder (under-used clips lead) and the writer's avoid-list, so the
    Writer steers away from over-shot setups from the FIRST draft instead of being
    rejected after. Thresholds mirror reviewer_macro (0.15 pair / 0.40 location)."""
    from collections import Counter
    ids: list[str] = []
    try:
        rows = con.execute(
            "SELECT payload_json FROM cards WHERE created_at >= datetime('now', ?) "
            "AND render_style='real_footage' ORDER BY created_at DESC LIMIT 40",
            (f"-{days} days",)).fetchall()
        for r in rows:
            try:
                pl = json.loads((r[0] if not isinstance(r, sqlite3.Row) else r["payload_json"]) or "{}")
            except Exception:
                continue
            ids += [c.get("asset_id") for c in (pl.get("cuts") or []) if c.get("asset_id")]
    except Exception as e:
        log.warning("recent_la_usage card fetch failed: %s", e)
        return Counter(), set(), set()
    if not ids:
        return Counter(), set(), set()
    try:
        q = con.execute(
            f"SELECT location_type, activity FROM assets WHERE asset_id IN "
            f"({','.join('?' for _ in ids)})", ids).fetchall()
    except Exception as e:
        log.warning("recent_la_usage asset fetch failed: %s", e)
        return Counter(), set(), set()
    la = [(r[0], r[1]) for r in q if r[0] and r[1]]
    freq = Counter(la)
    tot = sum(freq.values()) or 1
    overused = {k for k, n in freq.items() if n / tot >= 0.15}
    locf = Counter(loc for loc, _ in la)
    overused_loc = {loc for loc, n in locf.items() if n / tot >= 0.40}
    return freq, overused, overused_loc


def _lead_with_underused(rows: list, freq, overused_loc,
                         protect_ids: "set | None" = None) -> list:
    """Stable-sort a dict pool (each has 'loc'/'act') so UNDER-represented clips lead:
    over-used locations sink, then higher (loc,act) frequency sinks. Ties keep the
    incoming (diversity-sampled) order. The Writer anchors on whatever is at the top,
    so leading with fresh footage is the cheapest way to make it actually use it.

    PD 2026-06-17 (outing-unit freshness): `protect_ids` = clips that form a RICH unused
    OUTING (same day+place, already past cooldown = never shipped as an event). They are
    fresh AS AN EVENT even if their location is over-represented in aggregate — the
    repetition unit is the event, not the location — so they are NOT sunk for that."""
    protect_ids = protect_ids or set()
    def _key(v):
        loc, act = v.get("loc"), v.get("act")
        # un-groundable location (NULL/unknown/other) is not a useful lead — sink it.
        no_loc = 1 if (not loc or loc in ("unknown", "other")) else 0
        over = 0 if v.get("id") in protect_ids else (1 if loc in overused_loc else 0)
        # PD 2026-06-13: prefer FACE-SAFE clips within each freshness tier. The cap pushes
        # toward fresh locations, but cafe is human-heavy (12/16 have a bystander) and its
        # face-crop kept failing the no-human HARD RULE → render blocked. mom (8/9) and
        # outdoor (115/170) have plenty of no-human clips, so demoting has_human within a
        # tier surfaces face-safe fresh footage first without sacrificing freshness.
        human = 1 if v.get("has_human") else 0
        return (no_loc, over, human, freq.get((loc, act), 0))
    return sorted(rows, key=_key)


def _cap_overused_locations(rows: list, overused_loc, cap_frac: float = 0.34,
                            floor: int = 10, protect_ids: "set | None" = None) -> list:
    """Scarcity, not just ordering: cap over-used-location clips to ≤ cap_frac of the
    pool the Writer sees, so it CANNOT fill an episode with home/outdoor even though it
    keeps trying to. Reordering alone failed — the diverse sample is still ~44% home,
    so the Writer (which likes cozy home-rest) just picks from the plentiful tail. With
    a hard cap it runs out of home clips and must use the fresh ones. The over-used set
    refreshes daily, so the forced location rotates (cafe→mom→outdoor…) = variety ACROSS
    episodes. `rows` must be fresh-first (post _lead_with_underused). Keeps ≥ floor.

    PD 2026-06-17 (outing-unit freshness): `protect_ids` clips (a rich unused outing) count
    as fresh — never cap a coherent never-shipped event down to unusable just because its
    location is over-represented. Variety is "a different OUTING each episode", not a banned
    location, so a never-shipped 11-clip home day must survive intact and pickable."""
    if not overused_loc:
        return rows
    protect_ids = protect_ids or set()
    fresh = [v for v in rows if v.get("loc") not in overused_loc or v.get("id") in protect_ids]
    over = [v for v in rows if v.get("loc") in overused_loc and v.get("id") not in protect_ids]
    if not fresh:
        return rows  # nothing fresh to lean on — don't starve the Writer
    allowed = int(len(fresh) * cap_frac / max(1e-6, 1.0 - cap_frac))
    kept = fresh + over[:max(0, allowed)]
    if len(kept) < floor:  # safety: add over-used back until we reach the floor
        kept += over[max(0, allowed):max(0, allowed) + (floor - len(kept))]
    return kept


def _collapse_rf_same_clip_segments(c: dict, progress_cb: ProgressCb = None) -> int:
    """PD 2026-06-12: real_footage cuts must each be a DISTINCT clip. If the writer
    reused the SAME asset_id across cuts (slicing one clip into segments) those
    re-trim the same footage → "동일 구간 반복" (the cafe-loop). Merge same-asset cuts
    into ONE cut that plays the clip from its earliest trim_start for the COMBINED
    duration, captions re-spaced as time-scenes — the clip plays ONCE instead of
    restarting per cut. Returns how many cuts were collapsed."""
    cuts = c.get("cuts") or []
    if len(cuts) < 2:
        return 0
    by_aid: dict = {}
    order: list = []
    for cut in cuts:
        aid = cut.get("asset_id")
        if not aid:
            order.append(("_x", cut)); continue
        if aid not in by_aid:
            by_aid[aid] = []
            order.append((aid, None))
        by_aid[aid].append(cut)
    if all(len(v) <= 1 for v in by_aid.values()):
        return 0  # already all-distinct
    new_cuts: list = []
    collapsed = 0
    done: set = set()
    for aid, lone in order:
        if aid == "_x":
            new_cuts.append(lone); continue
        if aid in done:
            continue
        done.add(aid)
        grp = by_aid[aid]
        if len(grp) == 1:
            new_cuts.append(grp[0]); continue
        collapsed += len(grp) - 1
        base = dict(grp[0])
        starts = [float(g.get("trim_start") or 0) for g in grp]
        total = round(sum(float(g.get("duration_seconds") or g.get("trim_dur") or 5)
                          for g in grp), 1)
        base["trim_start"] = min(starts)
        base["duration_seconds"] = total
        base.pop("trim_dur", None)
        caps: list = []
        for g in grp:
            caps.extend(g.get("captions") or [])
        if caps:
            seg = total / len(caps)
            for i, cap in enumerate(caps):
                cap["start"] = round(i * seg + 0.2, 1)
                cap["end"] = round((i + 1) * seg - 0.1, 1)
        base["captions"] = caps
        new_cuts.append(base)
    if collapsed:
        c["cuts"] = new_cuts
        log.info("RF distinct-clip: collapsed %d same-clip segment cut(s) → %d cut(s)",
                 collapsed, len(new_cuts))
        if progress_cb:
            progress_cb(f":scissors: 같은 클립 segment {collapsed}개 합침 → "
                        f"{len(new_cuts)}개 distinct 컷 (반복 제거)")
    return collapsed


_RF_LOC_CAPTION_HINTS = {
    "outdoor": ["산책", "산책길", "산책로", "공원", "들판", "풀밭", "흙길", "오솔길", "그늘 아래"],
    "cafe": ["카페", "진열장", "디저트", "카운터", "테라스"],
    "home": ["거실", "소파", "주방", "식탁", "집 안", "방 안", "마루", "베란다"],
}


def _rf_loc_category(loc: str) -> str:
    loc = (loc or "").lower()
    if any(k in loc for k in ("outdoor", "park", "street", "walk", "garden", "야외")):
        return "outdoor"
    if "cafe" in loc or "카페" in loc:
        return "cafe"
    if any(k in loc for k in ("home", "living", "kitchen", "bedroom", "집", "거실")):
        return "home"
    return ""


def _rf_location_contradictions(concept: dict, con) -> list[str]:
    """PD 2026-06-12: use each clip's location_type to catch captions that
    CONTRADICT the clip (a cafe/home clip captioned as a "산책/그늘" outdoor walk —
    "말도 안 되는 내용"). NOT a location-coherence check: diverse locations are fine;
    only a caption whose implied place conflicts with the clip's actual place is
    flagged. Returns human-readable contradiction lines for the re-write feedback."""
    out: list[str] = []
    for i, cut in enumerate(concept.get("cuts") or []):
        aid = cut.get("asset_id")
        if not aid:
            continue
        try:
            r = con.execute("SELECT location_type FROM assets WHERE asset_id=?",
                            (aid,)).fetchone()
        except Exception:
            r = None
        loc_cat = _rf_loc_category(r[0] if r else "")
        if not loc_cat:
            continue
        caps = cut.get("captions") or []
        text = " ".join((c.get("ko") or "") for c in caps if isinstance(c, dict)) \
            or (cut.get("action") or "")
        implied = [k for k, ws in _RF_LOC_CAPTION_HINTS.items() if any(w in text for w in ws)]
        if implied and loc_cat not in implied:
            out.append(f"cut{i+1}: 캡션이 '{'/'.join(implied)}'를 말하는데 클립은 실제 "
                       f"'{loc_cat}' — 캡션을 이 클립의 위치/내용에 맞춰 다시 써라")
    return out


# PD 2026-06-16: present-age framing tokens (Ryani is 11) — wrong when slapped on a
# montage that is mostly ARCHIVAL footage ("11년차의 리듬" over 2016~2021 clips).
_RF_PRESENT_AGE_TOKENS = ("11년차", "11년 차", "11년째", "11살", "열한 살", "열한살",
                          "11년 동안", "11년을", "11년의")
# A caption/title carries a TIME ANCHOR when it names a past point or an explicit year.
# Presence of an anchor = the time shift is acknowledged (STEP 1.4 satisfied).
_RF_TIME_ANCHOR_TOKENS = ("년 전", "개월 전", "그때", "그 시절", "아기", "어릴", "어린",
                          "옛날", "처음", "첫날", "갓 왔", "막 왔", "그 시절",
                          "2016", "2017", "2018", "2019", "2020", "2021", "2022",
                          "2023", "2024", "2025")


def _rf_clip_year(aid: str) -> "int | None":
    """Year a clip was filmed, parsed from its asset_id (med_YYYY_MM_DD_…)."""
    m = re.match(r"med_(\d{4})_\d{2}_\d{2}", aid or "")
    return int(m.group(1)) if m else None


def _rf_session_key(aid: str) -> "str | None":
    """Shoot-SESSION key = the capture DATE (YYYY_MM_DD) from the asset_id.
    PD 2026-06-16: cooldown was exact-asset_id, so two clips from the SAME outing
    (med_2019_05_25_115132 vs …_115456 — same day, same waterside, ~3 min apart)
    both passed and the episode looked like a re-run of one already scheduled
    (water_peppy). Clips from the same day are almost always the same session for
    this footage; cooling the whole session kills the 'different id, same video' dup."""
    m = re.match(r"med_(\d{4}_\d{2}_\d{2})", aid or "")
    return m.group(1) if m else None


def _rf_cooldown_sessions(cooldown: "set[str]") -> "set[str]":
    """The shoot-session keys (capture dates) of every recently-used clip — so the
    whole session, not just the exact clip, is cooled."""
    return {k for k in (_rf_session_key(a) for a in cooldown) if k}


def _recently_used_rf_primary_sessions(con: sqlite3.Connection,
                                       n: int = RF_CLIP_COOLDOWN_EPISODES) -> "set[str]":
    """Shoot-sessions (capture dates) that were the PRIMARY outing of a recently
    PRODUCED RF card — defined as that card drawing >=2 cuts from that session.

    This is the reconciliation of two opposite past bugs:
      • 2026-06-17: cooling a whole session whenever ANY one clip of it was used was
        too coarse — a past episode that used a single flash from a rich outing locked
        the Writer out of ever building a proper montage from that outing.
      • 2026-06-22: cooling only the EXACT asset_id was too loose — a second episode
        (카페 fXIY) reused the SAME 2025-11-21 cafe outing as an earlier one via
        DIFFERENT files, so it looked like a re-run.
    Cooling only PRIMARY (>=2-cut) sessions of already-produced cards kills the
    same-outing re-run while leaving incidentally-touched and brand-new outings fully
    pickable. Counts produced drafts too (denylist state filter), so unuploaded
    same-outing dups are caught without depending on the published-only reviewer."""
    out: set[str] = set()
    try:
        _k = int(os.getenv("RF_COOLDOWN_RECENT_CARDS", str(max(n, 8) * 3)))
    except Exception:
        _k = max(n, 8) * 3
    if _k <= 0:
        return out
    try:
        rows = con.execute(
            "SELECT payload_json FROM cards WHERE render_style='real_footage' "
            "AND state NOT IN ('discarded','vetoed','failed','rejected') "
            "ORDER BY created_at DESC LIMIT ?", (_k,)).fetchall()
        for r in rows:
            try:
                p = json.loads(r[0] or "{}")
            except Exception:
                continue
            counts: dict[str, int] = {}
            for c in (p.get("cuts") or []):
                aid = c.get("asset_id") or (c.get("asset") or {}).get("asset_id")
                sk = _rf_session_key(aid) if aid else None
                if sk:
                    counts[sk] = counts.get(sk, 0) + 1
            out.update(sk for sk, ct in counts.items() if ct >= 2)
    except Exception as e:
        log.warning("primary-session cooldown lookup failed: %s", e)
    return out


def _rf_is_cooled(v: dict, cooldown: "set[str]", sessions: "set[str]",
                  vis_ids: "set[str] | None" = None) -> bool:
    """Cooled if the clip's EXACT asset_id was recently used.

    PD 2026-06-22: `sessions` is now the set of PRIMARY (>=2-cut) outings of already-
    produced cards (see _recently_used_rf_primary_sessions), NOT every touched session.
    Cool a clip if its exact id was used OR its outing was the primary subject of a past
    episode — this stops the same-outing re-run (카페 fXIY reused the 2025-11-21 cafe via
    different files) WITHOUT the 2026-06-17 over-coarseness (a single past flash no longer
    locks out a whole rich outing the Writer wants to build from fresh). The call site's
    >=6 relax still protects the Writer from being starved.

    PD 2026-06-25: `vis_ids` (from _rf_visual_cooldown_ids) cools clips that LOOK like
    recently-shipped footage — a different asset_id / different day whose appearance is a
    near-dup, which the id+session keys can't see. Same OR semantics: any one match cools."""
    vid = v.get("id") or v.get("asset_id")
    if bool(vid) and vid in cooldown:
        return True
    if vis_ids and bool(vid) and vid in vis_ids:
        return True
    sk = _rf_session_key(vid) if vid else None
    return bool(sk) and sk in sessions


def _rf_temporal_coherence(concept: dict, target_year: int) -> list[str]:
    """PD 2026-06-16: deterministic TEMPORAL-coherence gate (mirrors the location
    gate). The NON-NEGOTIABLE STEP 1.4 rule — when an episode mixes clips from
    different time periods, every period must be time-anchored and you must NOT
    frame an archival montage as the present — kept being IGNORED (a 2016~2021 clip
    jumble titled "11년차의 리듬"; PD: "과거잖아, 11년이 뭐야 … 시점이 뒤죽박죽"). Catch it
    post-write and force a rewrite. Two high-signal violations:
      A) PRESENT-AGE framing on a mostly-archival montage ("11년차/11살" + ≥2 past cuts).
      B) A multi-period jump (year span ≥2) with NO time anchor anywhere in the script.
    Clip year comes from asset_id (the date is encoded), so it works even when the
    Writer drops years_ago from the cut payload."""
    cuts = concept.get("cuts") or []
    years = [y for y in (_rf_clip_year(c.get("asset_id")) for c in cuts) if y]
    if len(years) < 2:
        return []
    archival = [y for y in years if target_year - y >= 1]
    text = (concept.get("title") or "") + " " + " ".join(
        (cap.get("ko") or "")
        for c in cuts for cap in (c.get("captions") or []) if isinstance(cap, dict))
    out: list[str] = []
    if len(archival) >= 2:
        hit = next((t for t in _RF_PRESENT_AGE_TOKENS if t in text), None)
        if hit:
            out.append(
                f"제목/캡션이 현재 나이 프레이밍('{hit}')을 쓰는데 클립 다수가 과거"
                f"({min(years)}~{max(years)}년)다 — 과거 몽타주를 현재 나이로 부르지 마라. "
                f"제목·캡션을 실제 촬영 시점('○년 전 [계절]의 랴니')으로 바꿔라.")
    if (max(years) - min(years) >= 2
            and not any(t in text for t in _RF_TIME_ANCHOR_TOKENS)):
        out.append(
            f"여러 시기({min(years)}~{max(years)}년) 클립이 섞였는데 시점 표시가 전혀 없다 — "
            f"'같은 날'인 척 금지(갑툭튀). 첫·끝 컷에 시점 앵커('○년 전'·'지금도')를 넣어라.")
    # C) PD 2026-06-16: a clip MONTHS older than the rest, inside a present-framed
    #    episode, whose OWN caption doesn't say when → a baby/past clip masquerading as
    #    today ("아기 레오 나왔을 때는 아기레오라고 해줘야지"). Leo is 8mo, so his 2025 baby
    #    clips are only ~6 months back — the year-span gate (B) misses them. Work in
    #    MONTHS from the asset_id and flag the specific cut.
    def _midx(aid):
        m = re.match(r"med_(\d{4})_(\d{2})", aid or "")
        return int(m.group(1)) * 12 + int(m.group(2)) if m else None
    midx = [_midx(c.get("asset_id")) for c in cuts]
    valid = [m for m in midx if m]
    if len(valid) >= 2:
        newest = max(valid)
        recent_ct = sum(1 for m in valid if newest - m <= 3)
        # only when the episode is MAJORITY-present (a "today" episode with a stray old
        # clip) — a real past↔present memory-lane (mostly old, anchored) is left alone.
        if recent_ct >= max(2, (len(valid) + 1) // 2):
            for i, c in enumerate(cuts):
                m = midx[i]
                if not m or newest - m < 6:
                    continue
                cap = " ".join((s.get("ko") or "")
                               for s in (c.get("captions") or []) if isinstance(s, dict))
                if not any(t in cap for t in _RF_TIME_ANCHOR_TOKENS):
                    out.append(
                        f"cut{i+1}: {_rf_clip_year(c.get('asset_id'))}년 클립(현재 회차에 섞인 "
                        f"과거/아기 시절)인데 그 컷 캡션에 시점 표시가 없다 — '아기 레오'·'○개월 "
                        f"전'·'그때'처럼 이 컷에서 시점을 밝혀라.")
    # D) PD 2026-06-17: a multi-PERIOD episode (year span ≥2) framed as ONE unfolding
    #    EVENT ("그날 저녁의 귀가", "문이 열리자", "만남 1초 전", "이제 곧 집") is fabricated
    #    continuity — different-time clips stitched as a single moment. PD (repeated):
    #    시점을 교차하려면 '하루 안 브이로그'(같은 날 클립) 또는 '그때는 이랬는데 지금은 이렇다'
    #    비교여야지 한 사건인 척 금지. Flag when year span ≥2 AND single-event continuity
    #    phrases appear. (Same-day multi-location transitions use span<2, so no conflict.)
    if max(years) - min(years) >= 2:
        _ev = next((p for p in ("문이 열리자", "문 앞", "1초 전", "만남", "이제 곧",
                                "곧이어", "그날 저녁", "그날 밤", "그날 아침", "방금")
                    if p in text), None)
        if _ev:
            out.append(
                f"여러 해({min(years)}~{max(years)}년) 클립을 '{_ev}'처럼 한 사건이 이어지는 "
                f"것으로 엮었다 — 시기가 다른 클립을 한 순간인 척 금지. 같은 날 클립만으로 '하루 "
                f"브이로그'를 쓰거나, '그때는 이랬는데 지금은 이렇다' 비교 구조로 다시 써라.")
    return out


def _propose_realfootage_singlepass(target: dt.date, context: dict,
                                     progress_cb: ProgressCb = None,
                                     prior_feedback: str = "") -> list[dict]:
    """PD 2026-06-04: dedicated lean real_footage storyteller. ONE LLM call
    that reads the clip ground truth and writes a flowing narrative grounded
    in what the clips actually show (쿠들습격 style, but honest).

    PD 2026-06-06: `prior_feedback` carries the Giri review's findings from a
    failed attempt so this re-proposal fixes them (the Giri-driven retry loop).
    """
    # PD 2026-06-13 (#1): one-take is ONE editing OPTION, not the RF default. The
    # decision is made by the AGENT (_should_onetake) reading the clips' content + the
    # editing JUDGMENT guide — NOT a random/деterministic coin flip ("자꾸 원테이크").
    # one-take is chosen only when a single clip's continuous moment is itself the
    # story; most episodes go to the normal montage writer below. RF_FORCE_ONETAKE=1
    # forces it (testing); =never disables.
    _ot_mode = os.getenv("RF_FORCE_ONETAKE", "auto")
    _ot_decision = ({"one_take": True, "clip_id": None} if _ot_mode == "1"
                    else _should_onetake(target, context) if _ot_mode != "never"
                    else {"one_take": False})
    if _ot_decision.get("one_take"):
        _longs = _rf_long_candidates(context)
        if _longs:
            # Route to the agent-chosen clip (fallback: longest available).
            _pick = next((c for c in _longs
                          if c.get("id") == _ot_decision.get("clip_id")), _longs[0])
            # PD 2026-06-12: on a long-clip day, pick a single-clip editing option —
            # one-take (one continuous segment) OR intra-clip montage (several segments
            # from the same clip). Deterministic ~50/50; if the preferred one yields
            # nothing (e.g. intra-clip found <2 segments) fall back to the other, then
            # to the normal montage writer below.
            import hashlib as _hl
            # PD 2026-06-12: intra-clip montage slices ONE clip into several
            # segment-cuts (all same asset_id) — on a near-static clip (e.g. a cafe
            # table shot) that reads as "동일 구간 무한 반복". PD: "반복으로 동영상을 짜르는게
            # 문제". DISABLE it by default; only one-take (ONE continuous segment, not
            # repeated) remains as the occasional single-clip option. Re-enable with
            # RF_INTRACLIP=1.
            _intra_on = os.getenv("RF_INTRACLIP", "0") == "1"
            _prefer_intra = (int(_hl.sha1(f"intra|{target.isoformat()}".encode())
                                 .hexdigest(), 16) % 2 == 0)
            if not _intra_on:
                _order = [_propose_realfootage_onetake]
            elif _prefer_intra:
                _order = [_propose_realfootage_intraclip, _propose_realfootage_onetake]
            else:
                _order = [_propose_realfootage_onetake, _propose_realfootage_intraclip]
            for _fn in _order:
                try:
                    res = _fn(target, context, _pick, progress_cb, prior_feedback)
                    if res:
                        return res
                except Exception as e:
                    log.warning("long-clip path %s failed: %s", _fn.__name__, e)
    if progress_cb:
        msg = ":pencil: real_footage 단일-패스 스토리텔러 (grounded flowing)"
        if prior_feedback:
            msg += " — 기리 피드백 반영 재작성"
        progress_cb(msg)
    system = REALFOOTAGE_SINGLEPASS_PROMPT.read_text(encoding="utf-8") + _editing_direction_block()
    # Feed both videos (Tier 1) and photos (Tier 2). PD 2026-06-06: photos are
    # NOT dropped anymore — every photo cut is animated via Seedance photo_i2v
    # so the writer can use a photo for the payoff/closer and still get motion.
    # The writer must mark photo cuts with source_hint="photo_i2v" + a
    # motion_prompt grounded in the photo.
    # PD 2026-06-06: exclude clips used in the last N real_footage episodes so
    # the same footage isn't reused back-to-back (4-episode cooldown).
    # PD 2026-06-13: drop PD-marked channel branding assets (bumper/promo) from the pool.
    _branding = _branding_asset_ids(_db())
    avail_videos = _drop_branding(context.get("available_videos", []), _branding)
    # PD 2026-06-11: MERGE old/archive footage into the main candidate pool. It used
    # to be a separate "memory-lane only" field, so RF kept re-using the same recent
    # ~28 clips (med_2026_05_25_144138 appeared in ALL 4 last episodes = "재탕") and
    # NEVER touched the years of older footage (2015/16 baby Ryani etc. — perfect for
    # the Ryani-intro). Merging gives a much bigger pool, so the cooldown can exclude
    # reused clips without starving (no more "relax → reuse"), and old footage becomes
    # a first-class pick. years_ago is already stamped for time-grounded captions.
    _arch = _drop_branding(context.get("archive_videos", []), _branding)
    _seen = {v.get("id") for v in avail_videos if v.get("id")}
    avail_videos = avail_videos + [v for v in _arch if v.get("id") and v.get("id") not in _seen]
    cooldown: set[str] = set()
    _cool_sessions: set[str] = set()
    _visual_cool: set[str] = set()
    try:
        _con = _db()
        cooldown = _recently_used_rf_assets(_con)
        # PD 2026-06-22: cool the PRIMARY outing of past episodes (>=2 cuts), not every
        # touched session — re-run prevention without 6/17 over-coarseness.
        _cool_sessions = _recently_used_rf_primary_sessions(_con)
        # PD 2026-06-25: VISUAL cooldown — cool any clip in ANY pool (videos+archive+
        # photos) that LOOKS like recently-shipped footage, so a fresh-id / different-day
        # near-dup ("쿨타임 지난 영상 자꾸 비슷하게") can't slip past the id+session keys.
        # Computed once over the union of every pool the Writer can pick from.
        if RF_VISUAL_COOLDOWN:
            _recent_vph = _recently_used_rf_vphashes(_con)
            _vis_pool_ids: set[str] = set()
            for _src in (avail_videos, context.get("archive_videos", []),
                         context.get("available_photos", []),
                         context.get("archive_photos", [])):
                for _v in (_src or []):
                    _id = _v.get("id") or _v.get("asset_id")
                    if _id:
                        _vis_pool_ids.add(_id)
            _visual_cool = _rf_visual_cooldown_ids(_con, _vis_pool_ids, _recent_vph)
        before = len(avail_videos)
        filtered = [v for v in avail_videos
                    if not _rf_is_cooled(v, cooldown, _cool_sessions, _visual_cool)]
        # Safety: don't starve the writer. If the cooldown leaves too few clips
        # for a full episode, relax it (still prefer fresh, but allow reuse).
        if len(filtered) >= 6:
            avail_videos = filtered
            if (cooldown or _visual_cool) and progress_cb:
                _vmsg = f" + 비슷한 룩 {len(_visual_cool)}개" if _visual_cool else ""
                progress_cb(f":snowflake: 최근 {RF_CLIP_COOLDOWN_EPISODES}편 사용 클립 "
                            f"{before - len(filtered)}개 제외 (쿨다운{_vmsg})")
        else:
            log.warning("cooldown left only %d clips (<6) — relaxing", len(filtered))
            if progress_cb:
                progress_cb(f":warning: 쿨다운 후 클립 부족({len(filtered)}개) — 완화 적용")
    except Exception as e:
        log.warning("cooldown filter failed: %s", e)
    # PD 2026-06-10: exclude clips already used by an EARLIER slot in THIS batch so
    # two same-day RF episodes don't come out near-identical (6/11 bug: both RF used
    # the exact same 7 photos). Best-effort — relax if it would starve the writer.
    _excl = set(context.get("exclude_asset_ids") or [])
    if _excl:
        _kept = [v for v in avail_videos
                 if v.get("id") not in _excl and v.get("asset_id") not in _excl]
        if len(_kept) >= 6:
            if len(_kept) < len(avail_videos) and progress_cb:
                progress_cb(f":twisted_rightwards_arrows: 배치 내 중복 회피 — 이미 쓴 클립 "
                            f"{len(avail_videos) - len(_kept)}개 제외")
            avail_videos = _kept
        else:
            log.warning("batch-dedup left only %d clips (<6) — relaxing", len(_kept))
    # PD 2026-06-11: merge OLD photos into the photo pool too (2500+ photos exist;
    # the recent ~50 + year-stratified old ones), and pass MANY more to the writer
    # (was [:10]) so RF actually has a big diverse library (video+photo, all years)
    # instead of re-using the same recent handful.
    _photos = _drop_branding(list(context.get("available_photos", [])), _branding)
    _pseen = {p.get("id") for p in _photos if p.get("id")}
    _photos += [p for p in _drop_branding(context.get("archive_photos", []), _branding)
                if p.get("id") and p.get("id") not in _pseen]
    # PD 2026-06-11: the exclude (batch-dedup) MUST also cover the PHOTO pool and
    # the separately-passed archive_videos field — the 6/12 RF 18:00 reused two
    # RF 08:00 clips because they re-entered as photo / raw-archive candidates that
    # the video-only filter above never touched (재탕 누수). Apply _excl to every
    # pool the writer can pick from, but never starve it (keep ≥6).
    # PD 2026-06-16: the recent-episode cooldown (whole-clip) must cover EVERY pool the
    # Writer can pick from — not just available_videos. It was applied to avail_videos
    # alone, so a cooled clip that ALSO lives in the archive/photo pool re-entered through
    # archive_videos, and the next episode reused water_peppy's exact 2018 water clip
    # (near-dup, caught only by the reviewer). Mirror the _excl treatment for cooldown:
    # exclude both from photos AND archive_videos. (Same leak class as the 6/12 _excl fix
    # — _excl got extended to every pool then, cooldown did not.)
    # PD 2026-06-16: cooldown is SESSION-aware (same-day outing), so apply it via
    # _rf_is_cooled to every pool; _excl (batch-dedup) stays exact-id.
    def _pool_excluded(v):
        if v.get("id") in _excl or v.get("asset_id") in _excl:
            return True
        return _rf_is_cooled(v, cooldown, _cool_sessions, _visual_cool)
    if _excl or cooldown or _visual_cool:
        _pk = [p for p in _photos if not _pool_excluded(p)]
        # keep ≥6 vs the BATCH-dedup floor only; cooled photos may shrink below that
        # (they're supplementary) but never empty the pool entirely.
        if len(_pk) >= 6 or (_pk and not _excl):
            _photos = _pk
    _arch_field = [v for v in context.get("archive_videos", [])
                   if not _pool_excluded(v)]
    # PD 2026-06-11: RF default = long-original ONE-TAKE. The writer kept ignoring
    # the prompt rule and montaging 6-9s trims even when 38s/75s clips sat in the
    # pool — a "label not rule" miss. So PRE-COMPUTE the long candidates and inject
    # them explicitly + sorted (longest first) so they can't be missed; the prompt
    # treats a non-empty list as a near-mandate to build a 1-2 cut one-take.
    # PD 2026-06-13 (writer-side): the diverse pool (A) only helps if the Writer USES
    # it — and the Writer anchors on whatever leads the list, which was the biggest
    # (home/resting) stratum. REORDER so UNDER-represented footage leads, and hand the
    # Writer an explicit avoid-list of over-shot (location, activity) setups. Together
    # these stop the "집-휴식으로 회귀" churn at the source instead of via reviewer reject.
    try:
        _la_freq, _la_over, _loc_over = _recent_la_usage(_db())
        # PD 2026-06-17 (outing-unit freshness): protect clips that form a RICH coherent
        # outing (same day+place, already past cooldown = never shipped as an event) from the
        # over-used-location demotion/cap. Variety comes from picking a DIFFERENT outing each
        # episode, not from banning a location — so a never-shipped 11-clip home day must stay
        # intact & near the top even though "home" is over-represented in aggregate.
        _protect_ids: set = set()
        if os.getenv("RF_EVENT_CLUSTERS", "1") == "1":
            _rich_min = int(os.getenv("RF_OUTING_RICH_MIN", "4"))
            for _o in _rf_event_clusters((avail_videos or []) + (_arch_field or [])):
                if (_o.get("n_clips") or 0) >= _rich_min:
                    _protect_ids.update(_o.get("clip_ids") or [])
        avail_videos = _lead_with_underused(avail_videos, _la_freq, _loc_over, _protect_ids)
        _photos = _lead_with_underused(_photos, _la_freq, _loc_over)
        # Hard scarcity cap so the Writer can't fill with over-used locations (reorder
        # alone wasn't enough — it kept picking home). Only when there's a real fresh
        # pool to fall back on; the floor keeps ≥6 usable. Rich unused outings are exempt.
        _cap = float(os.getenv("RF_OVERUSED_LOC_CAP", "0.34"))
        _before_cap = len(avail_videos)
        avail_videos = _cap_overused_locations(avail_videos, _loc_over, cap_frac=_cap,
                                               protect_ids=_protect_ids)
        _avoid_overused = {
            "over_used_locations": sorted(_loc_over),
            "over_used_location_activity": sorted(f"{l}/{a}" for (l, a) in _la_over),
            "note": ("최근 real_footage 업로드가 위 장소/활동에 과도하게 쏠려 있다. 단, "
                     "candidate_outings의 풍부한 묶음(같은 날·장소의 안 써본 사건)은 그 장소가 "
                     "과대표집이어도 우선 골라라 — 반복의 단위는 *장소*가 아니라 *이벤트*다. "
                     "고를 만한 풍부한 나들이가 없을 때만 다른 장소/활동(야외/카페/옛 영상)으로 "
                     "분산하라. 풀은 신선한 것부터 정렬돼 있다."),
        }
        if progress_cb and (_loc_over or _la_over):
            _capmsg = (f", 풀 {_before_cap}→{len(avail_videos)} 캡"
                       if len(avail_videos) < _before_cap else "")
            progress_cb(f":compass: 과대표집 회피 — 장소 {sorted(_loc_over)} 신선 우선{_capmsg}")
    except Exception as e:
        log.warning("writer-side underused reorder failed: %s", e)
        _avoid_overused = {}
    # PD 2026-06-14: bring SEGMENT-level freshness to the normal singlepass path. The
    # one-take path already picks a non-overlapping trim (_free_trim_start); singlepass
    # had ONLY whole-clip cooldown, so once that expired a reused clip could come back
    # with the same/overlapping trim ("동일 구간 반복"). Surface the recently-used windows
    # to the Writer (pick a different trim) and gate on it post-write below.
    try:
        _used_segs = _recently_used_rf_segments(
            _db(), days=int(os.getenv("RF_SEGMENT_HISTORY_DAYS", "60")))
    except Exception as e:
        log.warning("RF segment history lookup failed: %s", e)
        _used_segs = {}
    _pool_ids = {v.get("id") for v in (avail_videos + _arch_field) if v.get("id")}
    _reuse_segs = {aid: [[round(s, 1), round(e, 1)] for (s, e) in wins]
                   for aid, wins in _used_segs.items()
                   if aid in _pool_ids and wins}
    # PD 2026-06-17 (RF req #6): coherent OUTING bundles from the usable pool.
    _outings = (_rf_event_clusters((avail_videos or []) + (_arch_field or []))
                if os.getenv("RF_EVENT_CLUSTERS", "1") == "1" else [])
    if _outings and progress_cb:
        _top = _outings[0]
        progress_cb(f":busts_in_silhouette: 나들이 묶음 {len(_outings)}개 — "
                    f"최대 {_top['date']}/{_top['location']} {_top['n_clips']}컷")
    _LONG_MIN = float(os.getenv("RF_ONETAKE_MIN_SEC", "12"))
    _long = sorted(
        ({"id": v.get("id"), "dur": v.get("dur"),
          "sc": (v.get("sc") or "")[:160], "date": v.get("date")}
         for v in (avail_videos + _arch_field)
         if isinstance(v.get("dur"), (int, float)) and v.get("dur") >= _LONG_MIN),
        key=lambda v: -(v.get("dur") or 0))[:12]
    rf_context = {
        "target_date": target.isoformat(),
        # ⭐ long-original one-take candidates (12s+). If non-empty, DEFAULT to using
        # ONE of these whole as a 1-2 cut one-take instead of trimming a montage.
        "long_clip_candidates": _long,
        "available_videos": avail_videos,
        # PD 2026-06-13: photos ARE usable — a real photo as a ken-burns still has NO
        # drift (drift came only from Seedance photo_i2v GENERATION, now off by default
        # via RF_PHOTO_MODE=kenburns in cameraman). So keep photos in the pool — they're
        # essential for same-location past↔present bridges. RF_PHOTO_MODE=off bans them.
        "available_photos": [] if os.getenv("RF_PHOTO_MODE", "kenburns").lower() == "off"
                            else _photos[:100],
        # PD 2026-06-07: archive (older) clips for past⇄present memory-lane /
        # character-intro episodes. Each has years_ago — if you use one, the
        # caption MUST state the time point ("○년 전", "입양 첫날", "그때는…").
        "archive_videos": _arch_field,
        "archive_year_summary": context.get("archive_year_summary", {}),
        "video_date_clusters": context.get("video_date_summary", {}),
        # PD 2026-06-17 (RF req #6): coherent OUTING bundles — clips that are the SAME
        # day + SAME location = one real event. The Writer DEFAULTS to building a
        # video-first episode from ONE outing (처음→중간→끝) instead of stitching
        # unrelated cross-year clips into a fake single event ('각자의 방식'). Built from
        # the USABLE pool (post-cooldown/cap) so a surfaced outing is actually pickable.
        # Diversity lives BETWEEN outings; coherence WITHIN. RF_EVENT_CLUSTERS=0 reverts.
        "candidate_outings": _outings,
        # PD 2026-06-13: MACRO context (recent episodes + performance + audience comments)
        # so the writer AVOIDS repeating what we just shipped, from the very first draft.
        "macro_context_recent_uploads": context.get("macro_context", ""),
        # PD 2026-06-13 (writer-side): over-shot setups to AVOID + pool is pre-sorted
        # fresh-first. The Writer should pick from the top / away from these.
        "avoid_overused_setups": _avoid_overused,
        # PD 2026-06-13 (#3): same-LOCATION groups (past↔present pairing). If a past clip
        # and a current clip share a key (e.g. "seating:파란"), pair them for a visual
        # "그때 그 자리 → 지금" bridge — the system surfaces the match so the writer/PD
        # doesn't have to hunt for it.
        "same_location_groups": _rf_location_groups(
            (avail_videos or []) + (_photos or []) + (_arch_field or [])),
        # PD 2026-06-14: asset_id → [[start,end], ...] trim windows already used by
        # recent RF episodes. If you reuse one of these clips, pick a NON-overlapping
        # trim_start; overlapping reuse is rejected post-write (동일 구간 반복 방지).
        "recently_used_segments": _reuse_segs,
    }
    user = json.dumps(rf_context, ensure_ascii=False, default=str)
    # PD 2026-06-06: feed the showrunner directive (rolling ~1-month season plan
    # + what already aired across BOTH lanes) so the writer BUILDS the unified
    # arc. NOTE: arc is a POST-UPLOAD concern — arc.py functions no-op while
    # ARC_ENABLED != 1, so these calls add no cost until enabled.
    # PD 2026-06-07: inject authoritative character facts + PD-learned facts so
    # the writer doesn't invent traits (and asks via knowledge_questions instead).
    try:
        from agents import arc as _arc2, knowledge as _kn
        _facts = _arc2.CHARACTER_FACTS + _kn.facts_block(_db())
        try:
            from agents import pet_profile as _pp
            _facts += _pp.profile_block(_db())
        except Exception:
            pass
        if _facts:
            user += ("\n\n" + _facts +
                     "\n위 사실에 어긋나는 캐릭터 묘사 금지. 필요한데 모르는 건 "
                     "knowledge_questions에 적어라(추측 금지).")
    except Exception as e:
        log.warning("character facts injection (rf) failed: %s", e)
    # PD 2026-06-15: inject the PD-taste digest so CLIP/CUT SELECTION reflects PD's
    # accumulated choices (PD: "선택은 PD 과거 선택을 학습해서"). This is what stops the
    # 풀먹방→낮잠사진 drift at the selection point — the writer must pick clips PD's way.
    try:
        from agents import pd_taste as _pt
        _taste = _pt.taste_digest(_db(), lane="real_footage",
                                  kinds=(_pt.K_CLIP, _pt.K_CUT, _pt.K_CAPTION, _pt.K_TONE))
        if _taste:
            user += ("\n\n" + _taste +
                     "\n위 PD 취향을 클립/컷 선택과 캡션에 그대로 반영하라 — 특히 지정 컨셉을 "
                     "관련 실제 영상으로 채우고(다른 활동 사진으로 패딩 금지), 영상이 부족하면 컨셉을 바꿔라.")
    except Exception as e:
        log.warning("pd_taste injection (rf) failed: %s", e)
    _dir = ""
    try:
        from agents import arc as _arc
        _series = _arc.series_so_far(_db(), n=10)
        _dir = _arc.next_directive(_db(), today=target.isoformat(),
                                   render_style="real_footage") or ""
        if _series or _dir:
            user += "\n\n" + _series
            if _dir:
                user += ("\n\n## 오늘의 showrunner 디렉티브 (시즌 플랜 기반):\n" + _dir)
            user += ("\n위 시리즈/디렉티브를 이어받아라: 이미 한 소개/스토리 반복 금지, "
                     "열린 떡밥은 잇거나 회수, 이번 회차의 시리즈상 진전을 의식. "
                     "단 자산에 실제 있는 것만 — 디렉티브가 자산과 안 맞으면 자산 우선.")
    except Exception as e:
        log.warning("arc directive injection failed: %s", e)
    # PD 2026-06-16: RF concept-brainstorm was added to break the "각자의 방식" sameness, but
    # PD 2026-06-17 found it pushed the Writer into forced/dramatized concepts ("매복의 달인",
    # "침묵의 엄마") that don't fit the clips → captions fabricate, RF "산으로 갔다". PD's call:
    # turn the RF brainstorm OFF and go back to the writer-direct path (the "예전" good RF),
    # keeping the validated gates (subject-prominence, temporal, cooldown, photo-majority).
    # OFF by default now; RF_CONCEPT_BRAINSTORM=1 re-enables for experimentation.
    if (os.getenv("RF_CONCEPT_BRAINSTORM", "0") == "1"
            and not context.get("rf_storyline_seed")):
        try:
            from agents import concept_brainstorm as _cb
            _brief = (_dir or "").strip() or (
                "레오·랴니의 실제 보유 클립으로 만드는 짧은 일상/메모리레인 숏츠 — "
                "사건/주제가 뚜렷한 회차로(장소만 바꾼 '각자의 방식' 공존 관찰 금지).")
            _n = int(os.getenv("CONCEPT_BRAINSTORM_N", "5"))
            _res = _cb.best("real_footage", _brief, _n, context=context)
            _win = _res.get("winner")
            if _win:
                if progress_cb:
                    _rk = " | ".join(f"{c.get('audience_score')}:{c.get('title')}"
                                     for c in _res.get("ranking", [])[:_n])
                    progress_cb(f":brain: RF 컨셉 {_n}개 브레인스토밍 → 시청자 랭킹: {_rk}")
                    progress_cb(f":trophy: RF 승자({_win.get('audience_score')}/10): {_win.get('title')}")
                _beats = _win.get("beats") or []
                _beat_txt = " / ".join(str(b) for b in _beats) if isinstance(_beats, list) else str(_beats)
                context["rf_storyline_seed"] = (
                    "\n\n## ★리뷰어가 시청자 관점으로 고른 이번 회차 스토리라인 (이 앵글로 전개):\n"
                    f"제목: {_win.get('title')}\n로그라인: {_win.get('logline')}\n"
                    f"비트: {_beat_txt}\n"
                    "위 앵글을 실제 클립으로 충실히 전개하라. 단 자산에 실제 있는 것만 — "
                    "스토리라인이 보유 클립과 안 맞으면 클립 우선으로 가장 가까운 사건을 잡아라.")
        except Exception as e:
            log.warning("RF concept brainstorm failed (skipping): %s", e)
    if context.get("rf_storyline_seed"):
        user += context["rf_storyline_seed"]
    # Same-batch concept-dedup — works even though RF brainstorm is off by default
    # (RF_CONCEPT_BRAINSTORM=0). The singlepass Writer reads this directly.
    try:
        from agents import concept_brainstorm as _cb
        user += _cb._exclude_block(context)
    except Exception as e:
        log.warning("RF exclude_concepts inject failed: %s", e)
    if prior_feedback:
        user += (
            "\n\n## ⚠️ 이전 시도가 기리(Giri) 검수를 통과하지 못했다. 아래 지적을 "
            "반드시 고쳐서 다시 써라 (같은 실수 반복 금지):\n" + prior_feedback +
            "\n위 문제를 해결한 새 컨셉을 작성하라. 필요하면 컷 구성/자산/캡션을 바꿔라."
        )
    user += ("\n\nWrite ONE real_footage concept as a JSON array of length 1. "
             "Follow the 5-step order. Every caption must be grounded in the "
             "clip's actual sc. Flowing narrative, not dry list. Output ONLY "
             "the JSON array.")
    if _outings:
        user += ("\n\n⭐ 나들이 우선(candidate_outings): 각 항목은 같은 날·같은 장소의 클립 묶음 "
                 "= 하나의 실제 사건이고, 모두 최근에 *이벤트로 쓰지 않은* 것이다(쿨다운 통과). "
                 "기본값은 이 중 ONE 나들이(되도록 컷이 많은 풍부한 것)를 골라 그 안의 영상들로 "
                 "처음→중간→끝 video-first 에피소드를 짜는 것 — 서로 다른 날/해의 무관한 클립을 "
                 "이어 붙여 '하나의 사건'인 척하지 마라('각자의 방식' 금지). 그 나들이의 장소가 "
                 "avoid_overused_setups에 있어도 우선해도 된다 — 반복의 단위는 *장소*가 아니라 "
                 "*이벤트*다(같은 home이라도 안 써본 다른 날의 사건이면 새 콘텐츠). 여러 날을 "
                 "섞는 건 명시적 '그때 vs 지금' 비교일 때만 하고, 그때는 각 컷의 시점을 캡션에 라벨하라.")
    if _avoid_overused.get("over_used_locations") or _avoid_overused.get("over_used_location_activity"):
        user += ("\n\n⚠️ 신선도: available_videos는 신선한(덜 쓴) 클립부터 정렬돼 있다. "
                 "avoid_overused_setups의 장소/활동은 최근 과도하게 반복됐으니, 고를 만한 풍부한 "
                 "나들이가 없을 때는 컷의 과반을 그쪽으로 채우지 말고 야외/카페/옛 영상 등 다른 "
                 "장소·활동을 우선 골라라. (단 위 ⭐나들이 우선이 상위 규칙 — 풍부한 안 써본 "
                 "나들이가 있으면 그 장소가 과대표집이어도 그걸 골라라.)")
    if _reuse_segs:
        user += ("\n\n⚠️ 구간 재사용: recently_used_segments는 최근 에피소드가 이미 쓴 "
                 "(클립별) 구간이다. 같은 클립을 다시 쓰려면 그 구간과 겹치지 않는 다른 "
                 "trim_start를 골라라 — 겹치는 구간 재사용은 거부된다(동일 구간 반복 방지).")
    from agents.llm_cascade import call_text_cascade
    text = call_text_cascade(system, user, max_tokens=12000).strip()  # PD 2026-06-09: avoid truncation
    concepts = _robust_json_parse(text)
    # PD 2026-06-05: NO cut cap — script decides length.
    # PD 2026-06-06: stamp the single-pass author so the render pipeline can
    # SKIP the VLM post-render caption rewrite. The single-pass captions are
    # already grounded in clip ground truth; letting the Caption Agent rewrite
    # them downstream silently overwrote every prompt fix (the 3-day root cause).
    for c in concepts:
        c["render_style"] = "real_footage"
        c["author"] = "realfootage_singlepass"
        # PD 2026-06-12: ROOT FIX for the cafe-loop. The writer sometimes takes ONE
        # clip and slices it into N "segment" cuts (different trim_start of the SAME
        # asset_id) — on a near-static clip that reads as "동일 구간 무한 반복". RF cuts
        # must each be a DISTINCT clip; same-clip segment cuts are collapsed into a
        # SINGLE continuous-playthrough cut (captions re-spaced), so the clip plays
        # once instead of restarting per cut. PD: "반복으로 동영상을 짜르는게 문제".
        _collapse_rf_same_clip_segments(c, progress_cb)
        # PD 2026-06-08: do NOT force finale=video. A photo_i2v finale / Ryani
        # zoom is fine WHEN the quality (marking accuracy) is good — the quality
        # gate decides, not a blanket rule. (Earlier blanket auto-swap removed.)
    # PD 2026-06-12: deterministic location-CONTRADICTION gate. Use each clip's
    # location_type to catch captions that contradict the clip (a cafe/home clip
    # captioned as a "산책/그늘" outdoor walk — episode 193447). This is NOT a
    # coherence check — diverse locations are fine; only a caption whose implied
    # place conflicts with the clip's actual place is flagged. On contradiction,
    # re-write ONCE with the specifics as feedback ("[위치검증]" sentinel bounds it).
    if "[위치검증]" not in prior_feedback:
        try:
            _con_chk = _db()
            _contras: list = []
            for c in concepts:
                _contras += _rf_location_contradictions(c, _con_chk)
            _con_chk.close()
        except Exception as e:
            log.warning("RF location-contradiction check failed: %s", e)
            _contras = []
        if _contras:
            if progress_cb:
                progress_cb(f":mag: 위치검증 — 캡션이 클립 위치와 모순 {len(_contras)}건 → 재작성")
            _fb = ("[위치검증] 아래 컷의 캡션이 클립의 실제 위치/내용과 모순된다. 각 캡션을 "
                   "그 클립이 실제 보여주는 위치·내용에 맞춰 다시 써라(장소가 바뀌면 전환을 "
                   "캡션에 명시):\n" + "\n".join(_contras)
                   + (("\n\n[이전 피드백]\n" + prior_feedback) if prior_feedback else ""))
            return _propose_realfootage_singlepass(target, context, progress_cb,
                                                   prior_feedback=_fb)
    # PD 2026-06-16: deterministic TEMPORAL-coherence gate. The NON-NEGOTIABLE STEP 1.4
    # rule (mixed-period clips MUST be time-anchored; never frame an archival montage as
    # the present) kept being ignored — a 2016~2021 jumble titled "11년차". Catch it and
    # re-write ONCE ("[시점검증]" sentinel bounds the retry). RF_TEMPORAL_GATE=0 reverts.
    if (os.getenv("RF_TEMPORAL_GATE", "1") == "1"
            and "[시점검증]" not in prior_feedback):
        try:
            _tcon: list = []
            for c in concepts:
                _tcon += _rf_temporal_coherence(c, target.year)
        except Exception as e:
            log.warning("RF temporal-coherence check failed: %s", e)
            _tcon = []
        if _tcon:
            if progress_cb:
                progress_cb(f":mag: 시점검증 — 시점/나이 프레이밍 모순 {len(_tcon)}건 → 재작성")
            _fb = ("[시점검증] 아래 문제를 고쳐 다시 써라. 과거(다른 해) 클립을 쓰면 그 컷의 "
                   "시점을 실제 촬영 시기로 명시하고, 전체를 현재 나이('11년차' 등)로 부르지 "
                   "마라. 시점이 뒤섞이면 첫·끝 컷에 시점 앵커를 넣어 '같은 날'인 척하지 마라:\n"
                   + "\n".join(_tcon)
                   + (("\n\n[이전 피드백]\n" + prior_feedback) if prior_feedback else ""))
            return _propose_realfootage_singlepass(target, context, progress_cb,
                                                   prior_feedback=_fb)
    # PD 2026-06-14: SEGMENT-reuse gate (singlepass parity with the one-take path).
    # If a cut reuses a clip on a window that overlaps a recently-used segment of the
    # SAME clip → "동일 구간 반복"; re-write ONCE with the specifics ("[구간중복]" sentinel
    # bounds the retry). Toggle via RF_SEGMENT_GATE_NORMAL=0.
    if (os.getenv("RF_SEGMENT_GATE_NORMAL", "1") == "1"
            and "[구간중복]" not in prior_feedback and _used_segs):
        try:
            _overlaps2: list = []
            for c in concepts:
                _overlaps2 += _rf_segment_reuse_overlaps(c, _used_segs)
        except Exception as e:
            log.warning("RF segment-reuse check failed: %s", e)
            _overlaps2 = []
        if _overlaps2:
            if progress_cb:
                progress_cb(f":mag: 구간중복 — 같은 클립의 이미 쓴 구간 재사용 "
                            f"{len(_overlaps2)}건 → 재작성")
            _fb = ("[구간중복] 아래 컷이 최근 에피소드에서 이미 쓴 클립 구간과 겹친다. 같은 "
                   "클립을 쓰려면 겹치지 않는 다른 trim_start로 바꾸거나 다른 클립으로 교체하라:\n"
                   + "\n".join(_overlaps2)
                   + (("\n\n[이전 피드백]\n" + prior_feedback) if prior_feedback else ""))
            return _propose_realfootage_singlepass(target, context, progress_cb,
                                                   prior_feedback=_fb)
    # PD 2026-06-06: persist the stage artifact so we can trace WHERE a
    # problem (e.g. subject/object reversal) was introduced.
    try:
        art_dir = ROOT / "data" / "output" / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        art = art_dir / f"realfootage_{target.isoformat()}_{ts}.json"
        art.write_text(json.dumps({
            "stage": "realfootage_singlepass",
            "target_date": target.isoformat(),
            "input_videos": rf_context.get("available_videos"),
            "raw_llm_text": text,
            "parsed_concepts": concepts,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("artifact saved: %s", art.name)
    except Exception as e:
        log.warning("artifact save failed: %s", e)
    if progress_cb:
        n = len(concepts[0].get("cuts", [])) if concepts else 0
        progress_cb(f":white_check_mark: 단일-패스 완료 — {n} cuts (artifact 저장)")
    return concepts


# ──────────────────────────────────────────────────────────────────────
# Slack: post proposal
# ──────────────────────────────────────────────────────────────────────
def format_proposal_message(proposals: list[dict], target: dt.date) -> str:
    lines = [f"*내일({target.isoformat()}) 영상 제안 (2편)*\n"]
    for i, p in enumerate(proposals, 1):
        emoji = STYLE_EMOJI.get(p.get("render_style", ""), "🎬")
        title = p.get("title")
        title = title.get("ko") if isinstance(title, dict) else (title or "?")
        lines.append(f"{emoji} *{i}편 — {title}* ({p.get('render_style', '?')})")
        for cut in p.get("cuts", []):
            beat = cut.get("beat") or cut.get("tag") or "cut"
            desc = cut.get("description") or cut.get("action") or ""
            caps = cut.get("captions") or []
            if not desc and caps:
                desc = caps[0].get("ko", "")
            lines.append(f"  {beat}: {desc}")
        tone = p.get("tone", "")
        bgm = p.get("bgm_mood", "")
        lines.append(f"  톤: {tone} | BGM: {bgm}")
        lines.append("")
    lines.append("✏️ 수정/추가는 이 스레드에 답글. 2시간 내 응답 없으면 이대로 갑니다.")
    return "\n".join(lines)


def post_revised_proposal(thread_ts: str, proposals: list[dict],
                          target: dt.date, round_no: int) -> None:
    """PD 2026-06-07: re-post the REVISED concept into the same thread so PD can
    confirm or steer again (the propose→direction→update→re-confirm loop)."""
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel = os.environ.get("SLACK_WORKROOM_CHANNEL")
        if not channel:
            return
        msg = (f":pencil2: *수정안 v{round_no+1}* (피드백 반영)\n\n"
               + format_proposal_message(proposals, target)
               + "\n다른 방향 있으면 또 알려주세요. 없으면 이대로 진행합니다.")
        client.chat_postMessage(channel=channel, text=msg, thread_ts=thread_ts)
    except Exception as e:
        log.warning("post_revised_proposal failed: %s", e)


def post_proposal(proposals: list[dict], target: dt.date) -> str | None:
    """Post proposal to Slack workroom. Returns thread_ts."""
    from slack_sdk import WebClient
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    channel = os.environ.get("SLACK_WORKROOM_CHANNEL")
    if not channel:
        log.warning("SLACK_WORKROOM_CHANNEL not set, skipping Slack post")
        return None
    msg = format_proposal_message(proposals, target)
    resp = client.chat_postMessage(channel=channel, text=msg)
    return resp["ts"]


# ──────────────────────────────────────────────────────────────────────
# Slack: wait for PD feedback
# ──────────────────────────────────────────────────────────────────────
def wait_for_pd(thread_ts: str, timeout_sec: int = 7200,
                poll_interval: int = 60,
                progress_cb: ProgressCb = None,
                seen_ts: set | None = None) -> list[str]:
    """Poll Slack thread for PD replies. Returns list of reply texts.
    PD 2026-06-07: pass a persistent `seen_ts` across revision rounds so each
    round only catches NEW replies (the propose→revise→re-confirm loop)."""
    from slack_sdk import WebClient
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    channel = os.environ["SLACK_WORKROOM_CHANNEL"]
    bot_user_id = client.auth_test()["user_id"]

    deadline = time.time() + timeout_sec
    if seen_ts is None:
        seen_ts = {thread_ts}
    else:
        seen_ts.add(thread_ts)
    replies: list[str] = []

    while time.time() < deadline:
        try:
            resp = client.conversations_replies(channel=channel, ts=thread_ts)
            for msg in resp.get("messages", []):
                ts = msg["ts"]
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                # Skip bot's own messages
                if msg.get("user") == bot_user_id or msg.get("bot_id"):
                    continue
                text = (msg.get("text") or "").strip()
                if text:
                    replies.append(text)
                    log.info("PD reply: %s", text[:100])
                    # Check for immediate approval signals
                    if text.lower() in APPROVE_SIGNALS:
                        if progress_cb:
                            progress_cb(":white_check_mark: PD 즉시 승인!")
                        return replies

            # Check for emoji reactions on the proposal (👍 = approve)
            for msg in resp.get("messages", []):
                if msg["ts"] == thread_ts:
                    reactions = msg.get("reactions", [])
                    for r in reactions:
                        if r["name"] in ("+1", "thumbsup", "white_check_mark"):
                            if progress_cb:
                                progress_cb(f":white_check_mark: PD 리액션 승인 (:{r['name']}:)")
                            return replies

        except Exception as e:
            log.warning("Poll error: %s", e)

        if replies:
            # Got feedback, return it
            return replies

        remaining = int(deadline - time.time())
        if progress_cb and remaining > 0:
            progress_cb(f":hourglass: PD 대기 중... ({remaining // 60}분 남음)")
        time.sleep(poll_interval)

    if progress_cb:
        progress_cb(":robot_face: 타임아웃 — 자동 승인으로 진행합니다.")
    return replies


# ──────────────────────────────────────────────────────────────────────
# LLM: finalize concepts with PD feedback
# ──────────────────────────────────────────────────────────────────────
def finalize_concepts(proposals: list[dict], pd_feedback: list[str]) -> list[dict]:
    """If PD gave feedback, use LLM to apply edits. Otherwise return as-is."""
    if not pd_feedback:
        return proposals

    # Filter out pure approval signals
    real_feedback = [f for f in pd_feedback if f.lower() not in APPROVE_SIGNALS]
    if not real_feedback:
        return proposals

    from agents.llm_cascade import call_text_cascade
    system_msg = (
        "You are the Producer for Ryani & Leo channel. "
        "Apply the PD's feedback to modify the video concept proposals. "
        "Return the updated JSON array (same format as input). "
        "Keep concepts the PD didn't mention unchanged. "
        "Output JSON only, no commentary."
    )
    user_msg = json.dumps({
        "original_proposals": proposals,
        "pd_feedback": real_feedback,
    }, ensure_ascii=False)
    text = call_text_cascade(system_msg, user_msg, max_tokens=4096).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text)


# ──────────────────────────────────────────────────────────────────────
# Produce: Writer batch → Cameraman render
# ──────────────────────────────────────────────────────────────────────
def _render_realfootage_direct(concept: dict, target: dt.date,
                                con: sqlite3.Connection,
                                progress_cb: ProgressCb = None) -> Path | None:
    """Branch D (PD 2026-06-04): build a card directly from the single-pass
    real_footage concept (NO card-writer LLM, NO validator gauntlet) and
    render it. Preserves the grounded flowing captions verbatim."""
    import uuid
    from agents.writer import persist_card
    from agents.cameraman import render_card

    def _str(v):
        return v.get("ko") if isinstance(v, dict) else (v or "")

    title = _str(concept.get("title")) or "real_footage"
    cuts = concept.get("cuts") or []
    if not cuts:
        if progress_cb:
            progress_cb(":x: 단일-패스 컨셉에 cuts 없음")
        return None, None, None

    tone = concept.get("tone")
    if isinstance(tone, str):
        tone = {"primary": tone, "intensity": 0.6}
    elif not isinstance(tone, dict):
        tone = {"primary": "warm", "intensity": 0.6}

    card = {
        "card_id": str(uuid.uuid4()),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "author": "realfootage_singlepass",
        "card_type": "daily",
        "date": target.isoformat(),
        "theme": title,
        "title": title,
        "narrative_oneliner": _str(concept.get("narrative_oneliner")) or title,
        "render_style": "real_footage",
        "episode_format": "short",
        "tone": tone,
        "subjects": concept.get("subjects", ["leo"]),
        "duration_target_sec": sum(int(c.get("duration_seconds") or 4) for c in cuts) + 6,
        "writer_confidence": 0.85,
        "ask_pd": False,
        "cuts": cuts,
        "draft": {
            "title": title,
            "description": _str(concept.get("narrative_oneliner")) or title,
            "hashtags": ["#랴니", "#레오", "#일상"],
            "caption_burnin": title,
        },
    }

    run_cur = con.execute("INSERT INTO runs (agent, status) VALUES ('cameraman', 'running')")
    con.commit()
    persist_card(con, card, run_cur.lastrowid)
    con.execute("UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=?",
                (card["card_id"],))
    con.commit()

    if progress_cb:
        progress_cb(f":movie_camera: real_footage 렌더 시작: {title}")
    # use_brain=False — use the concept's cuts/asset_ids directly, no re-planning.
    out = render_card(card["card_id"], progress_cb=progress_cb, use_brain=False,
                      concept=concept)

    # PD 2026-06-06: real_footage was bypassing the Giri review gate entirely
    # (it goes through render_card directly, not render_with_retry). Run Giri on
    # the rendered episode, surface the verdict, persist the report. The retry
    # wrapper (_render_realfootage_with_retry) decides whether to re-generate.
    report = None
    if out:
        report = _giri_review_realfootage(out, concept, target, progress_cb)
    # #3: surface the Editor's intent↔footage verdict (persisted in render_meta) so the
    # retry wrapper can do an upstream re-propose on a `different_clip` mismatch.
    try:
        from agents.caption_salvage import _find_work_dir
        wd = _find_work_dir(card["card_id"])
        if wd:
            ep = json.loads((wd / "render_meta.json").read_text(
                encoding="utf-8")).get("_edit_plan")
            if ep:
                if isinstance(report, dict):
                    report["_edit_plan"] = ep
                elif report is None:
                    report = {"_edit_plan": ep}
    except Exception as e:
        log.warning("edit_plan surface failed: %s", e)
    return out, report, card["card_id"]


# Giri verdicts that count as "passed" (mirror reviewer.main exit logic).
GIRI_PASS_VERDICTS = ("업로드", "소폭 수정 후 업로드")


def _giri_feedback_to_text(report: dict) -> str:
    """Condense a Giri report into actionable Korean feedback for the writer."""
    if not report:
        return ""
    lines = []
    verdict = report.get("판정", "?")
    score = report.get("점수", "?")
    lines.append(f"기리 판정: {verdict} ({score}/10)")
    for m in (report.get("caption_vs_clip_mismatches") or [])[:6]:
        cn = m.get("cut_number", "?")
        cap = m.get("caption_text", "")
        real = m.get("what_clip_actually_shows", "")
        lines.append(f"- cut{cn} 캡션-클립 불일치: 캡션「{cap}」인데 실제로는 「{real}」")
    for key in ("개선점", "문제점", "이유", "총평"):
        v = report.get(key)
        if isinstance(v, list):
            for item in v[:5]:
                lines.append(f"- {key}: {item}")
        elif isinstance(v, str) and v.strip():
            lines.append(f"- {key}: {v.strip()[:200]}")
    return "\n".join(lines)


def _render_realfootage_with_retry(concept: dict, target: dt.date,
                                   con: sqlite3.Connection,
                                   context: dict,
                                   progress_cb: ProgressCb = None,
                                   max_attempts: int | None = None) -> Path | None:
    """PD 2026-06-06: real_footage MUST pass the Giri gate — if it fails, retry.
    Each retry re-runs the single-pass writer with the Giri feedback injected,
    then re-renders. Loops UNTIL it passes — PD does NOT tolerate low quality, so
    there is no small fixed cap (10, 100, however many it takes). A high safety
    ceiling (env RF_GIRI_MAX_ATTEMPTS, default 100) only prevents a true infinite
    loop / runaway cost if Giri can never be satisfied."""
    if max_attempts is None:
        # PD 2026-06-08: sane cap (was 100 — an unfixable Giri verdict looped for
        # hours and blocked the whole day's batch with 0 published).
        max_attempts = int(os.getenv("RF_GIRI_MAX_ATTEMPTS", "5"))
    cur_concept = concept
    last_out = None
    last_card_id = ""
    best_out = None
    best_card_id = ""
    best_key = (-1, -1)  # (intro_satisfied, score)
    attempt_outs: list = []   # PD 2026-06-08: every attempt writes episode_rf_*.mp4;
                              # delete the rejected ones so retries don't flood episodes/.
    _editor_loops = 0         # #3: bounded (≤EDITOR_MAX_LOOPS) editor-driven re-proposes
                              # when the Editor says the CLIP can't deliver the intent.

    def _finish(chosen):
        for p in attempt_outs:
            try:
                if chosen is None or str(p) != str(chosen):
                    Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        return chosen

    def _score_of(report) -> int:
        try:
            return int(report.get("점수") or report.get("score") or 0)
        except (TypeError, ValueError):
            return 0

    # PD 2026-06-08: on launch intro days the writer drifted to an outdoor-walk
    # story instead of a self-intro. Enforce: an intro day's concept MUST read as
    # a self-introduction (1인칭 자기소개 captions), else re-propose.
    intro_day = False
    try:
        from agents import arc as _arcchk
        intro_day = _arcchk._launch_intro_directive(target.isoformat(), "real_footage") is not None
    except Exception:
        pass

    def _is_self_intro(c: dict) -> bool:
        import re as _re
        blob = " ".join(
            (cap.get("ko", "") or "")
            for cut in (c.get("cuts") or []) for cap in (cut.get("captions") or []))
        # self-intro markers: greeting + first-person + age/identity
        pats = ["안녕", "나는", "저는", "소개", "예요", "이에요", "랴니예요", "레오예요"]
        hits = sum(1 for p in pats if p in blob)
        has_age = bool(_re.search(r"\d+\s*(살|개월)", blob))
        return hits >= 2 or (hits >= 1 and has_age)

    def _arc(card_id):
        # PD 2026-06-06: record the actually-rendered concept into the unified
        # arc ledger so the next directive (av+rf) builds on it.
        try:
            from agents import arc as _arcmod
            title = cur_concept.get("title")
            title = title.get("ko") if isinstance(title, dict) else (title or "real_footage")
            _arcmod.record_episode(con, card_id=card_id or "", date=target.isoformat(),
                                   render_style="real_footage", title=title,
                                   concept=cur_concept)
        except Exception as e:
            log.warning("arc record (rf) failed: %s", e)

    for attempt in range(1, max_attempts + 1):
        if progress_cb:
            progress_cb(f":repeat: real_footage 시도 {attempt}/{max_attempts}")
        out, report, card_id = _render_realfootage_direct(cur_concept, target, con, progress_cb)
        last_out = out or last_out
        last_card_id = card_id or last_card_id
        if out:
            attempt_outs.append(out)
        # track best-scoring attempt so we ship the best after the cap (not last).
        # NEVER track a face-violating attempt as best — a human face must not ship.
        intro_ok = (not intro_day) or _is_self_intro(cur_concept)
        # best key = (intro-satisfied, score) so on intro days a self-intro always
        # beats a non-intro, but a non-intro is still kept as last-resort.
        if out and not (report or {}).get("_face_violation"):
            key = (1 if intro_ok else 0, _score_of(report or {}))
            if key > best_key:
                best_key, best_out, best_card_id = key, out, card_id
        verdict = (report or {}).get("판정", "")
        # #3 upstream loop: if the Editor judged the CLIP itself can't deliver the
        # intent (different_clip), exclude that asset + re-propose with the editor's
        # note — BEFORE accepting/salvaging this render. Bounded to EDITOR_MAX_LOOPS.
        _mm = ((report or {}).get("_edit_plan") or {}).get("intent_mismatch") or {}
        if (str(_mm.get("suggestion", "")).startswith("different_clip")
                and _editor_loops < int(os.getenv("EDITOR_MAX_LOOPS", "2"))
                and attempt < max_attempts):
            _editor_loops += 1
            _bad = _mm.get("asset_id") or ""
            if _bad:
                context.setdefault("exclude_asset_ids", [])
                if _bad not in context["exclude_asset_ids"]:
                    context["exclude_asset_ids"].append(_bad)
            _enote = (f"[편집자 피드백] 이 클립({_bad})로는 의도를 담을 수 없다. 화면은 "
                      f"실제로 「{_mm.get('what_footage_shows','')}」인데 의도는 "
                      f"「{_mm.get('what_intent_said','')}」였다. 다른 클립으로 다시 써라.")
            if progress_cb:
                progress_cb(f":scissors: 편집자: 클립 부적합 → 다른 클립 재제안 "
                            f"({_editor_loops}/{os.getenv('EDITOR_MAX_LOOPS','2')})")
            try:
                new_concepts = _propose_realfootage_singlepass(
                    target, context, progress_cb, prior_feedback=_enote)
                if new_concepts:
                    cur_concept = new_concepts[0]
            except Exception as e:
                log.warning("editor re-propose failed: %s", e)
            continue  # render the new concept on the next attempt
        if not report:
            # Review unavailable (e.g., no API key) — don't loop blindly.
            log.warning("Giri report unavailable — accepting attempt %d", attempt)
            _arc(card_id)
            return _finish(out)
        if verdict in GIRI_PASS_VERDICTS and intro_ok:
            if progress_cb:
                progress_cb(f":white_check_mark: 기리 통과 (시도 {attempt}): {verdict}")
            _arc(card_id)
            return _finish(out)
        if verdict in GIRI_PASS_VERDICTS and not intro_ok and progress_cb:
            progress_cb(":repeat: 기리는 통과했지만 자기소개 회차가 아님 — 재생성")
        # PD 2026-06-11 캡션-salvage: a caption-shaped Giri fail does NOT need a full
        # re-render. Rewrite the captions on THIS already-rendered episode (VLM ground-
        # truth, no Seedance) and re-review before spending another render attempt.
        if out and intro_ok and verdict not in GIRI_PASS_VERDICTS:
            try:
                from agents import caption_salvage as _csv
                if _csv.is_caption_fixable(report):
                    salv = _csv.salvage(card_id, report, progress_cb=progress_cb)
                    if salv and Path(salv).exists():
                        attempt_outs.append(salv)
                        srep = _giri_review_realfootage(salv, cur_concept, target, progress_cb)
                        sverdict = (srep or {}).get("판정", "")
                        if sverdict in GIRI_PASS_VERDICTS:
                            if progress_cb:
                                progress_cb(f":white_check_mark: 캡션-salvage 후 기리 통과: {sverdict}")
                            _arc(card_id)
                            return _finish(salv)
                        if srep and not srep.get("_face_violation"):
                            skey = (1 if intro_ok else 0, _score_of(srep))
                            if skey > best_key:
                                best_key, best_out, best_card_id = skey, salv, card_id
            except Exception as e:
                log.warning("caption salvage attempt (rf) failed: %s", e)
        if attempt >= max_attempts:
            # Never ship a face-violating episode — if every attempt had a face,
            # best_out is None → skip this slot entirely (no episode > a face).
            if best_out is None:
                if progress_cb:
                    progress_cb(f":no_entry: {max_attempts}회 모두 얼굴 노출/렌더 실패 — 슬롯 비움(발행 안 함)")
                log.warning("real_footage: all %d attempts face-violating/failed — skipping slot",
                            max_attempts)
                return _finish(None)
            if progress_cb:
                progress_cb(f":warning: 기리 미통과 {max_attempts}회 — 최고({'자기소개' if best_key[0] else '일반'} {best_key[1]}/10) 결과 사용")
            log.warning("real_footage failed Giri after %d attempts — using best %s",
                        max_attempts, best_key)
            _arc(best_card_id)
            return _finish(best_out)
        # Re-propose with feedback and retry.
        feedback = _giri_feedback_to_text(report)
        if intro_day and not intro_ok:
            feedback = ("[자기소개 회차 강제] 이번 회차는 반드시 캐릭터 '자기소개'다. "
                        "산책/먹방/일상 관찰 스토리로 빠지지 마라. 캡션은 1인칭 자기소개"
                        "('안녕! 나는 레오예요, 8개월이에요' / '나는 랴니, 11살 누나')로 써라. "
                        "실제 클립이 산책이어도 자기소개 톤으로 프레이밍하라.\n") + feedback
        if progress_cb:
            progress_cb(f":arrows_counterclockwise: 기리 미통과({verdict}) — 피드백 반영 재생성")
        try:
            # PD 2026-06-13: the Giri retry must ALSO satisfy the MACRO Reviewer — else a
            # Giri-"safe" re-proposal drifts back to a recently-shipped theme (the nap
            # montage Giri loves). Inject macro context so the writer avoids repetition,
            # then Reviewer-gate the re-proposal (bounded, so freshness ⊻ quality can't
            # loop forever).
            from agents import reviewer_macro as _rv
            _macro = _rv.fetch_macro_context(con)
            context["macro_context"] = _rv.macro_context_text(_macro)
            new_concepts = _propose_realfootage_singlepass(
                target, context, progress_cb, prior_feedback=feedback)
            for _r in range(int(os.getenv("REVIEWER_RETRY_REWRITES", "2"))):
                if not new_concepts:
                    break
                _v = _rv.run_reviewer(new_concepts, _macro, "real_footage", progress_cb)
                if _v.get("pass"):
                    break
                new_concepts = _propose_realfootage_singlepass(
                    target, context, progress_cb,
                    prior_feedback=feedback + "\n[Reviewer 거시 — 최근 업로드와 차별화] "
                    + (_v.get("rewrite_directive") or ""))
            if new_concepts:
                cur_concept = new_concepts[0]
        except Exception as e:
            log.warning("re-propose failed (%s) — keeping prior concept", e)
    return _finish(last_out)


def _giri_review_realfootage(video: Path, concept: dict, target: dt.date,
                             progress_cb: ProgressCb = None) -> dict | None:
    """Run the Giri review agent on a rendered real_footage episode and save
    the report as an artifact. Non-fatal: logs + returns None on any failure."""
    try:
        from agents.reviewer import review as giri_review

        def _capstr(cut):
            caps = cut.get("captions") or []
            ko = " / ".join(c.get("ko", "") for c in caps if c.get("ko"))
            return ko or cut.get("action", "")

        storyboard = [
            {"beat": c.get("beat", f"cut{i+1}"),
             "description": f"{c.get('action','')} | 캡션: {_capstr(c)}"}
            for i, c in enumerate(concept.get("cuts") or [])
        ]
        if progress_cb:
            progress_cb(":mag: 기리 검수 중 (real_footage)...")
        report = giri_review(video, storyboard=storyboard, concept=concept)
        # PD 2026-06-08 HARD RULE: no human face may ship. The review VLM under-
        # reports faces (called a bench man "lower body"), so run a dedicated
        # dense face scan — but ONLY when some cut's clip has a human (else skip
        # the ~30s cost). A detected face forces 수정 필요 + _face_violation, and
        # the retry loop will NOT publish a face-violating episode.
        try:
            con_fc = _db()
            has_human_cut = False
            for c in concept.get("cuts") or []:
                aid = c.get("asset_id")
                if not aid:
                    continue
                r = con_fc.execute("SELECT has_human FROM assets WHERE asset_id=?", (aid,)).fetchone()
                if r and r[0]:
                    has_human_cut = True
                    break
            if has_human_cut:
                from agents.facecheck import video_has_face
                if progress_cb:
                    progress_cb(":detective: 얼굴 노출 검사 중 (has_human 컷 있음)...")
                face, _n = video_has_face(video)
                # PD 2026-06-12: the dense scan short-circuits on ONE frame and has
                # false-positived (called legs/lower-body a face) — that forced a
                # clean 업로드 episode (164341, Giri 9/10, "최소수정안 없음") into a worse
                # re-render. When the Giri LLM ITSELF concluded upload-clean (no
                # human-face problem), require a STRICTER confirmation (≥2 frames)
                # before invoking the HARD face rule.
                if face:
                    _giri_clean = (str(report.get("최종_결정", "")).strip() == "업로드"
                                   and "얼굴" not in str(report.get("가장_큰_문제", "")))
                    if _giri_clean:
                        face, _n = video_has_face(video, min_hits=2)
                        if not face and progress_cb:
                            progress_cb(":mag: 얼굴 1프레임 의심됐으나 정밀확인(≥2프레임) 미검출 "
                                        "+ Giri 업로드판정 — 오탐 처리(업로드 유지)")
                if face:
                    report["판정"] = "수정 필요"
                    report["_face_violation"] = True
                    report["가장_큰_문제"] = "인간 얼굴 노출 — crop 실패 (HARD RULE)"
                    report.setdefault("개선점", [])
                    if isinstance(report.get("개선점"), list):
                        report["개선점"].insert(0, "인간 얼굴 노출 — crop_out 강화 또는 해당 클립 교체")
                    if progress_cb:
                        progress_cb(":no_entry: 얼굴 노출 감지 — 발행 차단")
        except Exception as e:
            log.warning("face check failed (non-fatal): %s", e)
        score = report.get("점수", "?")
        verdict = report.get("판정", "?")
        mismatches = report.get("caption_vs_clip_mismatches") or []
        # PD 2026-06-06: post the SAME formatted Giri report ai_vtuber posts, so
        # real_footage leaves the same rich Slack log (was just a one-liner).
        if progress_cb:
            try:
                from agents.reviewer import format_slack_report
                progress_cb(format_slack_report(report))
            except Exception:
                progress_cb(f":clipboard: 기리 판정: {verdict} ({score}/10)"
                            + (f" — 캡션-클립 불일치 {len(mismatches)}건" if mismatches else ""))
        log.info("Giri real_footage: %s (%s/10), %d caption mismatches",
                 verdict, score, len(mismatches))
        # Persist as artifact (PD: 단계별 산출물 추적).
        try:
            art_dir = ROOT / "data" / "output" / "artifacts"
            art_dir.mkdir(parents=True, exist_ok=True)
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            art = art_dir / f"giri_realfootage_{target.isoformat()}_{ts}.json"
            # default=str: the reviewer mixes numpy scalars (image-similarity
            # metrics) into the report, which plain json.dumps rejects.
            art.write_text(json.dumps({
                "stage": "giri_review_realfootage",
                "video": str(video),
                "score": score,
                "verdict": verdict,
                "report": report,
            }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log.info("artifact saved: %s", art.name)
        except Exception as e:
            log.warning("giri artifact save failed: %s", e)
        return report
    except Exception as e:
        log.warning("Giri review (real_footage) failed: %s", e)
        if progress_cb:
            progress_cb(f":warning: 기리 검수 실패(무시하고 진행): {str(e)[:100]}")
        return None


def produce_and_render(concepts: list[dict], target: dt.date,
                       progress_cb: ProgressCb = None,
                       dry_run: bool = False) -> list[Path]:
    """Generate cards via Writer and render via Cameraman for each concept."""
    from agents.writer import call_llm, validate_card, persist_card
    from agents.cameraman import render_card

    con = _db()
    outputs = []

    for i, concept in enumerate(concepts, 1):
        gen_mode = concept.get("generation_mode", "")

        # Cameraman Validator gate (PD 2026-06-02): if verdict='blocked',
        # skip this concept before spending Seedance $$$. revise = log + go.
        validation = concept.get("cameraman_validation") or {}
        verdict = validation.get("verdict", "")
        if verdict == "blocked":
            issues = validation.get("issues") or []
            tier1 = [iss for iss in issues if iss.get("tier") == 1]
            log.warning(
                "Cameraman Validator BLOCKED concept '%s' — %d Tier-1 issues: %s",
                concept.get("title", "?")[:60], len(tier1),
                [(iss.get("cut_tag"), iss.get("type"), iss.get("description")[:80])
                 for iss in tier1[:3]],
            )
            if progress_cb:
                progress_cb(
                    f":no_entry: [{i}/{len(concepts)}] Validator blocked: "
                    f"{validation.get('summary','')[:80]} — concept 건너뜀"
                )
            continue
        elif verdict == "revise":
            log.info("Cameraman Validator REVISE: proceeding but logging "
                     "%d issues for next iteration",
                     len(validation.get("issues") or []))
            if progress_cb:
                progress_cb(
                    f":warning: [{i}/{len(concepts)}] Validator revise "
                    f"({validation.get('score_1_10','?')}/10) — 진행은 함"
                )

        # ── Photo Selection: ALWAYS run, even for t2v (as background reference) ──
        if progress_cb:
            progress_cb(f":camera: [{i}/{len(concepts)}] 배경 참조용 사진 선정 중: {concept.get('title', '?')}")
        try:
            from agents.photo_selector import select_photos
            selected = select_photos(concept, n_select=10)
            if selected:
                if gen_mode == "text_to_video":
                    # t2v: photos are BACKGROUND REFERENCE only — inject scene_description
                    # into veo_prompt so it describes REAL backgrounds, not imagined ones
                    bg_descriptions = []
                    for sel in selected[:5]:
                        desc = sel.get("scene_description", sel.get("caption_ko", ""))
                        bg = sel.get("background", "")
                        if desc or bg:
                            bg_descriptions.append(f"{desc} (배경: {bg})" if bg else desc)
                    if bg_descriptions:
                        concept["_background_references"] = bg_descriptions
                        if progress_cb:
                            progress_cb(f":house: 실제 배경 참조 {len(bg_descriptions)}개 수집")
                else:
                    # i2v / ref / interp: photos are pose/background reference.
                    # Map photos onto Director-authored cuts ONLY — do NOT
                    # silently append spurious cuts that lack Director metadata
                    # (seedance_mode, motion_prompt, regen_prompt). Extra photos
                    # beyond the cut count are discarded; the cuts the Director
                    # designed are the source of truth.
                    #
                    # PD 2026-06-02 CRITICAL FIX: previously this loop wrote
                    # `concept["cuts"][j]["asset_id"] = sel.get("asset_id")`
                    # UNCONDITIONALLY, silently replacing Writer's clip pick
                    # with whatever photo_selector chose. Validator had already
                    # approved based on Writer's asset_id (which matched the
                    # Writer's action), then photo_selector swapped in a
                    # different clip — captions described X, video showed Y.
                    # Root cause of every "왜 캡션이랑 영상이 안 맞아?" complaint
                    # in this debugging session. Now: only set asset_id if
                    # Writer left it blank.
                    cut_count = len(concept.get("cuts", []))
                    for j, sel in enumerate(selected[:cut_count]):
                        cut = concept["cuts"][j]
                        if not cut.get("asset_id"):
                            cut["asset_id"] = sel.get("asset_id")
                        # PD 2026-06-02: stamp asset's location_type onto the
                        # cut so Caption Agent + Validator can detect space
                        # transitions and demand narrator bridges. Only when
                        # we're using photo_selector's pick (Writer's pick
                        # already has its own location_type stamped earlier).
                        if not cut.get("location_type"):
                            loc = sel.get("location_type") or ""
                            if loc:
                                cut["location_type"] = loc
                                cur_space = (cut.get("space") or "").lower()
                                if not cur_space or cur_space not in loc.lower() and loc.lower() not in cur_space:
                                    cut["space"] = loc
                        if not cut.get("description") and sel.get("caption_ko"):
                            cut["description"] = sel["caption_ko"]
                    extras = max(0, len(selected) - cut_count)
                    if progress_cb:
                        msg = f":white_check_mark: {min(len(selected), cut_count)}장 선정 완료"
                        if extras:
                            msg += f" ({extras}장은 컷 수 초과로 제외)"
                        progress_cb(msg)
        except Exception as e:
            log.warning("Photo selection failed: %s", e)
            if progress_cb:
                progress_cb(f":warning: 사진 선정 실패: {str(e)[:100]}")

        # Branch D (PD 2026-06-04): real_footage bypasses the card-writer LLM
        # entirely. The single-pass storyteller already produced grounded
        # flowing captions + cuts. Re-writing through writer_system.md was
        # re-dramatizing them ("식탁 위의 범인 (대반전)"). Build the card
        # directly from the concept, preserving title/captions/cuts verbatim.
        if (concept.get("render_style") or "").lower() == "real_footage":
            if progress_cb:
                progress_cb(f":zap: [{i}/{len(concepts)}] real_footage 직접 카드화 (card-writer 우회) + 기리 retry")
            try:
                # PD 2026-06-06: render with the Giri-driven retry loop. context
                # is rebuilt so a retry can re-run the single-pass writer with
                # the failed attempt's Giri feedback injected.
                rf_context = _gather_context(con, target)
                # PD 2026-06-11: carry the batch exclusions (stamped by launch onto
                # the concept) into the retry context, so a Giri-retry re-propose
                # keeps avoiding the other slot's clips (else 시도2 re-picked them).
                _bx = concept.get("_batch_exclude_asset_ids")
                if _bx:
                    rf_context["exclude_asset_ids"] = sorted(
                        set(rf_context.get("exclude_asset_ids") or []) | set(_bx))
                out = _render_realfootage_with_retry(
                    concept, target, con, rf_context, progress_cb)
                if out:
                    outputs.append(out)
            except Exception as e:
                log.exception("real_footage direct render failed: %s", e)
                if progress_cb:
                    progress_cb(f":x: real_footage 렌더 실패: {str(e)[:150]}")
            continue

        if progress_cb:
            progress_cb(f":pencil: [{i}/{len(concepts)}] Writer 카드 생성: {concept.get('title', '?')}")

        # Build a hint-enriched user prompt for the Writer
        hint = {
            "instructions": (
                "Produce one Concept Card v2 JSON for the given concept. "
                "Keep ALL string fields SHORT — maxLength limits are hard caps. "
                "rationale/notes max 80 chars. No optional fields unless essential. "
                "Omit sticker_additions and hero_motion unless explicitly needed."
            ),
            "target_date": target.isoformat(),
            "concept_hint": concept,
        }

        try:
            system = (ROOT / "prompts" / "writer_system.md").read_text(encoding="utf-8")
            from agents.writer import strip_fences
            card = None
            for attempt in range(3):
                os.environ["WRITER_MAX_TOKENS"] = "16384"
                card_text = call_llm(system, json.dumps(hint, ensure_ascii=False))
                try:
                    card = json.loads(strip_fences(card_text))
                    break
                except json.JSONDecodeError:
                    log.warning("JSON parse failed (attempt %d), retrying...", attempt + 1)
            if card is None:
                # PD 2026-06-08: the card-writer LLM returning bad JSON (esp. when
                # network degradation forces the Anthropic fallback) used to KILL
                # the whole av slot. The Writer/Director concept already has the
                # cuts/title/etc., so build the card straight from it (like rf's
                # Branch D bypass) — the setdefault + concept-propagation below
                # fill the rest. av no longer dies on a flaky card-writer response.
                log.warning("card-writer JSON failed — building card from concept directly")
                if progress_cb:
                    progress_cb(":wrench: card-writer JSON 실패 — 컨셉으로 직접 카드화(폴백)")
                card = {}

            # Fill in defaults that the LLM may omit
            import uuid
            card.setdefault("card_id", str(uuid.uuid4()))
            card.setdefault("created_at", dt.datetime.now(dt.timezone.utc).isoformat())
            card.setdefault("author", "writer_agent")
            card.setdefault("date", target.isoformat())
            card.setdefault("theme", concept.get("title", "untitled"))
            card.setdefault("card_type", "daily")
            # Force valid card_type
            if card.get("card_type") not in ("daily", "memory_lane"):
                card["card_type"] = "daily"
            card.setdefault("narrative_oneliner", concept.get("title", ""))
            card.setdefault("duration_target_sec", 20)
            card.setdefault("writer_confidence", 0.8)
            card.setdefault("ask_pd", False)
            card.setdefault("fallback_plan", "이전 에셋으로 대체")
            card.setdefault("ai_augmentation", {"needed": False, "type": "none"})

            # tone: Director v2 sometimes emits a bare string ('warm' / 'fun');
            # downstream persist_card calls tone.get(), so coerce to dict shape.
            tone_val = card.get("tone")
            if isinstance(tone_val, str):
                card["tone"] = {"primary": tone_val, "intensity": 0.7}
            elif not isinstance(tone_val, dict):
                card["tone"] = {"primary": "warm", "intensity": 0.7}
            else:
                card["tone"].setdefault("primary", "warm")
                card["tone"].setdefault("intensity", 0.7)

            # background_plan: setdefault won't merge required sub-keys into
            # a partial dict from the Director. Merge each required field.
            bg_defaults = {
                "target_background_id": "auto",
                "perceptual_hash": "0000000000000000",
                "differs_from_previous": True,
                "week_distribution_check": "pass",
            }
            bg = card.get("background_plan")
            if not isinstance(bg, dict):
                card["background_plan"] = dict(bg_defaults)
            else:
                for k, v in bg_defaults.items():
                    bg.setdefault(k, v)

            # recommended_assets: Writer v2 often writes role as free text
            # ('intro hero — Leo on sofa stretching'). Schema enum requires
            # one of: primary / supporting / transition / fallback. Coerce.
            assets = card.get("recommended_assets")
            if not isinstance(assets, list):
                card["recommended_assets"] = []
            else:
                for idx, a in enumerate(assets):
                    if not isinstance(a, dict):
                        continue
                    r = a.get("role")
                    if r not in ("primary", "supporting", "transition", "fallback"):
                        a["role"] = "primary" if idx == 0 else "supporting"
            # draft: same partial-dict merge problem as background_plan —
            # Director may write only some fields, setdefault doesn't fill the
            # rest. Merge required sub-fields in.
            draft_defaults = {
                "title": concept.get("title", "Ryani & Leo"),
                "description": concept.get("title", ""),
                "hashtags": ["#랴니", "#레오", "#펫"],
                "caption_burnin": concept.get("title", ""),
            }
            d = card.get("draft")
            if not isinstance(d, dict):
                card["draft"] = dict(draft_defaults)
            else:
                for k, v in draft_defaults.items():
                    d.setdefault(k, v)

            # Override render_style from concept
            card["render_style"] = concept.get("render_style")
            card["date"] = target.isoformat()
            # Producer cards skip PD review — go straight to approved
            card["ask_pd"] = False

            # Propagate episode_format + one-take consolidated cuts from the
            # Writer/Director concept. The card-writer LLM stage tends to
            # re-expand cuts back to "shorts = 4 cuts", which defeats the
            # one-take pivot. Source-of-truth = the concept that came out of
            # _consolidate_short_to_one_take.
            if concept.get("episode_format"):
                card["episode_format"] = concept["episode_format"]
            # Propagate scene/time context from concept — the LLM card stage
            # tends to drop these fields. Cameraman + writer_director need
            # them for scene_setter caption, set_library lookup, etc.
            for field in ("episode_time", "episode_date", "set_anchor",
                          "set_description", "chain_mode", "wink_subject"):
                if concept.get(field):
                    card[field] = concept[field]
            concept_cuts = concept.get("cuts") or []
            if concept_cuts:
                card["cuts"] = concept_cuts
                if concept.get("episode_format") == "short":
                    card["duration_target_sec"] = (
                        sum(int(c.get("target_duration_seconds")
                                or c.get("duration_seconds") or 5)
                            for c in concept_cuts) + 6  # ~6s bumpers
                    )

            errors = validate_card(card)
            if errors:
                log.warning("Card validation errors: %s", errors[:3])

            if not dry_run:
                run_cur = con.execute(
                    "INSERT INTO runs (agent, status) VALUES ('writer', 'running')"
                )
                con.commit()
                persist_card(con, card, run_cur.lastrowid)
                con.execute(
                    "UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=?",
                    (card["card_id"],),
                )
                con.commit()
                log.info("Card %s state=approved", card["card_id"][:16])

                # Render with auto-retry loop
                if progress_cb:
                    progress_cb(f":movie_camera: [{i}/{len(concepts)}] 렌더 + 검수 루프: {concept.get('title', '?')}")
                try:
                    from agents.retry_loop import render_with_retry
                    # PD 2026-06-08/10 COST CONTROL: each av retry re-renders ALL
                    # Seedance i2v cuts (real $) — and that COMPOUNDS with the per-cut
                    # gate retries and the self-heal rounds (the 6/10 test hit ~246
                    # Seedance cuts = ~$100 when this was 3). Back to 1: a Giri-failed
                    # AV is saved for PD review (not re-rendered ×N). Cheap recovery
                    # (Validator-block re-propose, no Seedance) lives in launch's slot
                    # loop; the hard SEEDANCE_MAX_CALLS ceiling backstops everything.
                    out, review_report = render_with_retry(
                        card["card_id"], concept,
                        # PD 2026-06-10: 0 = render ONCE, no whole-episode re-render
                        # (that redid all 6 cuts incl. their per-cut heals — the cost
                        # multiplier). A Giri-fail is SAVED for PD review (save-AV),
                        # not re-rendered. Per-cut quality = _gate_and_heal (1 try).
                        max_retries=int(os.getenv("AV_MAX_RETRIES", "0")),
                        progress_cb=progress_cb,
                    )
                    # PD 2026-06-10: an AV render that PASSED Giri publishes; one that
                    # rendered but FAILED Giri must NOT auto-publish (junk) — but the
                    # mp4 must be SAVED, not discarded, so PD can review/salvage it.
                    # render_with_retry leaves the file on disk; we surface its path and
                    # keep it out of `outputs` (publish list) when the verdict failed.
                    _verdict = (review_report or {}).get("판정", "")
                    _passed = _verdict in GIRI_PASS_VERDICTS if review_report else bool(out)
                    if out and _passed:
                        outputs.append(out)
                        try:
                            con.execute("UPDATE episode_stories SET use_count = use_count + 1")
                            con.commit()
                        except Exception:
                            pass
                    elif out:
                        # PD 2026-06-11 캡션-salvage: before saving an AV render as
                        # "failed", check if Giri failed only on CAPTIONS — if so,
                        # rewrite the captions on this render (no Seedance) and re-review.
                        # A pass → publish; else fall through to save-for-PD.
                        salvaged = False
                        try:
                            from agents import caption_salvage as _csv
                            if _csv.is_caption_fixable(review_report):
                                salv = _csv.salvage(card["card_id"], review_report,
                                                    progress_cb=progress_cb)
                                if salv and Path(salv).exists():
                                    from agents.reviewer import review as _giri_av
                                    srep = _giri_av(salv,
                                                    storyboard=concept.get("cuts", []),
                                                    concept=concept)
                                    if (srep or {}).get("판정") in GIRI_PASS_VERDICTS:
                                        outputs.append(salv)
                                        salvaged = True
                                        if progress_cb:
                                            progress_cb(f":white_check_mark: AV 캡션-salvage "
                                                        f"후 기리 통과 → 게시: {salv}")
                        except Exception as e:
                            log.warning("AV caption salvage failed: %s", e)
                        # rendered but not publishable — saved for PD review
                        if not salvaged:
                            try:
                                from pathlib import Path as _P
                                if _P(out).exists() and progress_cb:
                                    progress_cb(f":floppy_disk: AV 영상 저장됨(미게시, 검수 "
                                                f"'{_verdict or '미통과'}'): {out}")
                            except Exception:
                                pass
                except ImportError:
                    # Fallback if retry_loop not available
                    out = render_card(card["card_id"], progress_cb=progress_cb, use_brain=True, concept=concept)
                    outputs.append(out)
            else:
                log.info("[dry-run] Would produce card: %s", card.get("theme"))

        except Exception as e:
            log.exception("Failed to produce concept %d: %s", i, concept.get("title"))
            if progress_cb:
                progress_cb(f":x: {i}편 실패: {str(e)[:200]}")

    return outputs


# ──────────────────────────────────────────────────────────────────────
# Photo reminder
# ──────────────────────────────────────────────────────────────────────
def send_photo_reminder() -> None:
    """Post a photo upload reminder to the workroom."""
    from slack_sdk import WebClient
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    channel = os.environ.get("SLACK_WORKROOM_CHANNEL")
    if not channel:
        return
    hour = dt.datetime.now(KST).hour
    if hour < 10:
        greeting = "좋은 아침이에요!"
    elif hour < 15:
        greeting = "점심 잘 드셨나요?"
    else:
        greeting = "오늘도 수고하셨어요!"

    client.chat_postMessage(
        channel=channel,
        text=(
            f":camera_with_flash: {greeting}\n"
            f"랴니&레오 사진/영상 올려주세요! "
            f"올리면 자동으로 에셋 DB에 등록됩니다."
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Main: daily_pipeline
# ──────────────────────────────────────────────────────────────────────
# YouTube auto-upload (PD 2026-06-07: 승인 후 자동 + 예약 공개 publishAt).
YOUTUBE_PUBLISH_HOUR = int(os.getenv("YOUTUBE_PUBLISH_HOUR", "18"))  # KST


def _ensure_upload_columns(con: sqlite3.Connection) -> None:
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
        if "uploaded" not in cols:
            con.execute("ALTER TABLE cards ADD COLUMN uploaded INTEGER DEFAULT 0")
        if "youtube_video_id" not in cols:
            con.execute("ALTER TABLE cards ADD COLUMN youtube_video_id TEXT")
        if "youtube_publish_at" not in cols:
            con.execute("ALTER TABLE cards ADD COLUMN youtube_publish_at TEXT")
        con.commit()
    except Exception as e:
        log.warning("ensure upload columns failed: %s", e)


def _compute_publish_at(target: dt.date) -> str:
    """Scheduled-public time as ISO-UTC. target date at YOUTUBE_PUBLISH_HOUR KST,
    but always at least 1h in the future (YouTube requires publishAt > now)."""
    from zoneinfo import ZoneInfo
    kst = ZoneInfo("Asia/Seoul")
    when = dt.datetime.combine(target, dt.time(YOUTUBE_PUBLISH_HOUR, 0), tzinfo=kst)
    now = dt.datetime.now(kst)
    if when <= now + dt.timedelta(hours=1):
        when = now + dt.timedelta(hours=1)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _auto_upload_episode(con: sqlite3.Connection, out_path: Path, target: dt.date,
                         progress_cb: ProgressCb = None,
                         publish_at_iso: str | None = None) -> str | None:
    """Upload a rendered episode to YouTube as SCHEDULED-PUBLIC (private +
    publishAt). Sets cards.uploaded=1 + youtube_video_id (activates arc/cooldown).
    OAuth not bootstrapped → warn + skip (non-fatal).

    publish_at_iso: explicit scheduled-public time (ISO-UTC). Launch mode passes
    a per-slot timeslot time; daily mode leaves it None → YOUTUBE_PUBLISH_HOUR."""
    _ensure_upload_columns(con)
    row = con.execute(
        "SELECT card_id, payload_json, theme FROM cards WHERE output_video_path=? "
        "ORDER BY updated_at DESC LIMIT 1", (str(out_path),),
    ).fetchone()
    if not row:
        log.warning("auto-upload: no card for %s", out_path)
        return None
    card_id = row["card_id"]
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    draft = payload.get("draft", {})
    # Channel Manager packaging (Phase 1): generate a hook title + SEO description +
    # concept-specific hashtags, rotating 3 tone arms as an experiment. This runs ONLY
    # here — at upload time — so a Giri-failed render never spends an LLM call on it.
    # Falls back to the static draft if the LLM is down so an upload never blocks.
    title = desc = None
    tags = []
    try:
        from agents.channel_manager import make_packaging
        # payload top-level carries the concept (title/oneliner/cuts) for normal cards;
        # pinned cards have only draft.title — make_packaging degrades gracefully.
        concept = dict(payload)
        concept.setdefault("title", draft.get("title") or row["theme"])
        pkg = make_packaging(concept, card_id=card_id)
        title, desc = pkg["title"], pkg["description"]
        tags = [str(t).lstrip("#") for t in pkg["hashtags"]]
        draft["packaging_arm"] = pkg["arm"]          # record arm for perf attribution
        payload["draft"] = draft
        con.execute("UPDATE cards SET payload_json=? WHERE card_id=?",
                    (json.dumps(payload, ensure_ascii=False), card_id))
    except Exception as e:
        log.warning("packaging failed for %s, using static draft: %s", card_id[:8], e)
    if not title:                                     # fallback: legacy static draft
        title = draft.get("title") or row["theme"] or "Ryani & Leo"
        if isinstance(title, dict):
            title = title.get("ko") or "Ryani & Leo"
        desc = draft.get("description", "") or ""
        tags = [str(t).lstrip("#") for t in (draft.get("hashtags") or [])]
    publish_at = publish_at_iso or _compute_publish_at(target)
    try:
        from youtube.upload import upload_short
        if progress_cb:
            progress_cb(f":arrow_up: YouTube 예약 업로드 중: {Path(out_path).name} "
                        f"(공개 예정 {publish_at})")
        res = upload_short(out_path, title, desc, tags=tags,
                           publish_at_iso=publish_at)
        vid = res.get("id")
        # PD 2026-06-24: auto-pick the best frame and set it as the channel thumbnail.
        # Best-effort — a thumbnail failure must never fail the upload.
        try:
            import scripts.pick_thumbnail as _pt
            from youtube.upload import set_thumbnail
            _thumb = Path(out_path).with_suffix(".thumb.jpg")
            _pk = _pt.make_thumbnail(out_path, _thumb, concept={"theme": title})
            set_thumbnail(vid, _thumb)
            if progress_cb:
                progress_cb(f":frame_with_picture: 썸네일 자동 설정 — {_pk.get('reason','')[:60]}")
        except Exception as _te:
            log.warning("thumbnail set failed for %s (non-fatal): %s", vid, _te)
    except Exception as e:
        log.warning("auto-upload failed for %s: %s", card_id[:8], e)
        if progress_cb:
            progress_cb(f":warning: YouTube 업로드 실패 (OAuth 미설정일 수 있음): "
                        f"{str(e)[:120]} — `python -m youtube.oauth` 부트스트랩 필요")
        return None
    con.execute(
        "UPDATE cards SET state='published', uploaded=1, youtube_video_id=?, "
        "youtube_publish_at=?, updated_at=datetime('now') WHERE card_id=?",
        (vid, publish_at, card_id),
    )
    con.commit()
    if progress_cb:
        progress_cb(f":white_check_mark: 예약 업로드 완료 → https://youtube.com/shorts/{vid} "
                    f"(공개 {publish_at})")
    return vid


def resolve_knowledge_questions(concepts: list[dict], target: dt.date, *,
                                ask_cb: Callable[[list[dict]], dict] | None = None,
                                progress_cb: ProgressCb = None) -> list[dict]:
    """Layer ③ (PD 2026-06-07): if the concept stage emitted knowledge_questions
    it couldn't ground, surface them to PD instead of letting inventions through.

    - dedup vs already-known (character_facts), record new ones as pending.
    - WEEK 1 (is_launch_week) + ask_cb available → BLOCKING: ask PD, store answers,
      re-propose so the answer lands in THIS batch.
    - otherwise → NON-BLOCKING: post the questions, proceed (writer already avoided
      the uncertain element); answers seed future episodes via /answer.
    Returns concepts (possibly re-proposed)."""
    try:
        from agents import knowledge as kn
    except Exception:
        return concepts
    con = _db()
    qs = kn.collect_questions(concepts)
    new_qs = [q for q in qs if not kn.has_question(con, q["question"])]
    if not new_qs:
        return concepts
    for q in new_qs:
        kn.add_pending(con, q.get("subject", ""), q["question"])
    blocking = kn.is_launch_week(target.isoformat()) and ask_cb is not None
    qlines = "\n".join(f"  {i+1}. {q['question']}" for i, q in enumerate(new_qs))
    if progress_cb:
        progress_cb(f":grey_question: 컨셉이 확신 못 한 캐릭터/세계 사실 {len(new_qs)}건 — "
                    + ("PD 답 대기(블로킹)" if blocking else "스레드 질문(논블로킹, /answer로 답)")
                    + f"\n{qlines}")
    if not blocking:
        return concepts  # non-blocking: pending saved, proceed as-is
    # blocking: ask PD, store answers, re-propose
    try:
        answers = ask_cb(new_qs) or {}
    except Exception as e:
        log.warning("ask_cb failed: %s", e)
        answers = {}
    stored = 0
    for q in new_qs:
        a = answers.get(q["question"]) or answers.get(str(new_qs.index(q) + 1))
        if a:
            kn.add_answer(con, q["question"], a, subject=q.get("subject", ""))
            stored += 1
    if stored and progress_cb:
        progress_cb(f":white_check_mark: PD 답 {stored}건 지식 저장 — 컨셉 재생성")
    if stored:
        # re-propose with the new facts now injected (one fresh draw per lane)
        try:
            style = (concepts[0].get("render_style") if concepts else None)
            ctx = _gather_context(con, target)
            return propose_concepts(target, ctx, style_filter=style,
                                    progress_cb=progress_cb) or concepts
        except Exception as e:
            log.warning("re-propose after answers failed: %s", e)
    return concepts


def daily_pipeline(target: dt.date, *,
                   timeout_sec: int = 7200,
                   progress_cb: ProgressCb = None,
                   on_thread_created: Callable[[str], None] | None = None,
                   video_cb: Callable[[Path], None] | None = None,
                   style_filter: str | None = None,
                   dry_run: bool = False) -> None:
    con = _db()

    # 1. Gather context
    if progress_cb:
        progress_cb(f":calendar: 일일 파이프라인 시작 — {target.isoformat()}")
    context = _gather_context(con, target)

    # 2. Propose concepts
    if progress_cb:
        progress_cb(":bulb: 2편 컨셉 제안 생성 중...")
    proposals = propose_concepts(target, context, style_filter=style_filter,
                                 progress_cb=progress_cb)
    log.info("Proposed %d concepts", len(proposals))

    if dry_run:
        print(format_proposal_message(proposals, target))
        print("\n[dry-run] Would wait for PD, then produce + render.")
        # Save to DB anyway for tracking
        con.execute(
            "INSERT INTO daily_proposals (target_date, proposal_json, status) VALUES (?, ?, 'proposed')",
            (target.isoformat(), json.dumps(proposals, ensure_ascii=False)),
        )
        con.commit()
        return

    # 3. Post to Slack
    if timeout_sec == 0 and progress_cb:
        # Test mode — post proposal inside existing thread via progress_cb
        progress_cb(format_proposal_message(proposals, target))
        thread_ts = None
    else:
        # Normal mode — post proposal as new thread
        thread_ts = post_proposal(proposals, target)
        if on_thread_created and thread_ts:
            on_thread_created(thread_ts)

    con.execute(
        "INSERT INTO daily_proposals (target_date, proposal_json, thread_ts, status) VALUES (?, ?, ?, 'proposed')",
        (target.isoformat(), json.dumps(proposals, ensure_ascii=False), thread_ts),
    )
    con.commit()
    proposal_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 4-5. PD feedback → REVISION LOOP (PD 2026-06-07): a confirm-only flow is
    # not enough — if PD replies a different direction in the thread, revise the
    # concept, RE-POST the updated version, and wait again. Loop until PD
    # approves (or stops replying / max rounds / timeout).
    final = proposals
    all_feedback: list[str] = []
    if timeout_sec > 0 and thread_ts:
        seen: set = {thread_ts}
        max_rounds = int(os.getenv("PD_REVISION_ROUNDS", "5"))
        for rnd in range(max_rounds):
            if progress_cb:
                progress_cb(":hourglass: PD 피드백 대기 (확정 또는 다른 방향 제안)...")
            fb = wait_for_pd(thread_ts, timeout_sec=timeout_sec,
                             progress_cb=progress_cb, seen_ts=seen)
            real = [f for f in fb if f.lower() not in APPROVE_SIGNALS]
            all_feedback += fb
            if not real:
                # approval / reaction / timeout → settle on current `final`
                break
            if progress_cb:
                progress_cb(":memo: 다른 방향 반영해 컨셉 업데이트 중...")
            final = finalize_concepts(final, fb)
            post_revised_proposal(thread_ts, final, target, rnd)
    elif progress_cb:
        progress_cb(":fast_forward: 테스트 모드 — PD 컨펌 스킵")

    con.execute(
        "UPDATE daily_proposals SET pd_feedback=?, finalized_json=?, status='confirmed' WHERE id=?",
        (json.dumps(all_feedback, ensure_ascii=False),
         json.dumps(final, ensure_ascii=False),
         proposal_id),
    )
    con.commit()

    # 6. Produce + render
    if progress_cb:
        progress_cb(":factory: 2편 생산 시작!")
    outputs = produce_and_render(final, target, progress_cb=progress_cb)

    # 7. Update status
    con.execute(
        "UPDATE daily_proposals SET status='produced' WHERE id=?",
        (proposal_id,),
    )
    con.commit()

    # 8. Notify + POST THE ACTUAL VIDEOS (PD 2026-06-07: 결과 동영상은 꼭
    # 올라와야 한다 — used to upload, regressed to filenames-only).
    if progress_cb:
        progress_cb(
            f":white_check_mark: {len(outputs)}편 렌더 완료!\n"
            + "\n".join(f"  • `{o.name}`" for o in outputs)
        )
    if video_cb:
        for o in outputs:
            try:
                video_cb(o)
            except Exception as e:
                log.warning("video_cb failed for %s: %s", o, e)

    # 9. AUTO-UPLOAD (PD 2026-06-07: 승인 후 자동 + 예약 공개). Only in the
    # PD-approval flow (timeout_sec>0) — NOT /test (timeout=0). Schedules each
    # episode public via publishAt. OAuth missing → warns + skips. Gate with
    # YOUTUBE_AUTO_UPLOAD=0 to disable.
    if (timeout_sec > 0 and not dry_run
            and os.getenv("YOUTUBE_AUTO_UPLOAD", "1") == "1"):
        if progress_cb:
            progress_cb(":satellite: 승인 완료 — YouTube 예약 업로드 시작")
        for o in outputs:
            try:
                _auto_upload_episode(con, o, target, progress_cb)
            except Exception as e:
                log.warning("auto-upload failed for %s: %s", o, e)
    elif progress_cb:
        progress_cb("`/upload <card_id>`로 수동 업로드할 수 있어요.")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Producer — daily 3-video pipeline")
    p.add_argument("--date", default=None,
                   help="target date YYYY-MM-DD (default: tomorrow KST)")
    p.add_argument("--dry-run", action="store_true",
                   help="propose only, no Slack/render")
    p.add_argument("--timeout", type=int, default=7200,
                   help="PD wait timeout in seconds (default: 7200 = 2h)")
    p.add_argument("--no-slack", action="store_true",
                   help="skip Slack proposal thread + PD wait (iteration mode). "
                        "Renders straight-through using auto-approved concept. "
                        "Internally sets timeout=0.")
    p.add_argument("--style", choices=["ai_vtuber", "real_footage"], default=None,
                   help="produce only this style (default: both ai_vtuber + real_footage)")
    p.add_argument("--remind", action="store_true",
                   help="send photo reminder and exit")
    args = p.parse_args()

    if args.no_slack:
        args.timeout = 0

    if args.remind:
        send_photo_reminder()
        return 0

    if args.date:
        target = dt.date.fromisoformat(args.date)
    else:
        target = (dt.datetime.now(KST) + dt.timedelta(days=1)).date()

    def _print(msg: str) -> None:
        print(msg)

    try:
        daily_pipeline(target, timeout_sec=args.timeout,
                       progress_cb=_print, style_filter=args.style,
                       dry_run=args.dry_run)
        return 0
    except Exception as e:
        log.exception("Producer failed")
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
