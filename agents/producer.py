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
    return con


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

    best_photos = [
        {"id": r["asset_id"], "act": r["activity"] or "", "sub": r["subjects_csv"] or "",
         "mood": r["mood"] or "", "bg": r["background"] or "",
         "sc": _ground_truth_sc(r),
         **_extra_vlm(r)}
        for r in con.execute(
            """
            SELECT asset_id, activity, subjects_csv, mood, background, scene_description, pd_notes,
                   notes
            FROM assets
            WHERE vlm_analyzed_at IS NOT NULL AND kind='photo'
                  AND quality_score >= 0.7 AND file_path NOT LIKE '%.heic'
                  AND (decoration_level IS NULL OR decoration_level = 'none')
            ORDER BY has_human ASC, quality_score DESC, captured_iso DESC
            LIMIT 70
            """,
        ).fetchall()
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
    best_videos = [
        {"id": r["asset_id"], "act": r["activity"] or "", "sub": r["subjects_csv"] or "",
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
        for r in con.execute(
            """
            SELECT asset_id, activity, subjects_csv, mood, scene_description, pd_notes,
                   duration_sec, captured_iso, location_type, notes, has_human, lighting,
                   composition, focus_subject
            FROM assets
            WHERE vlm_analyzed_at IS NOT NULL AND kind='video' AND quality_score >= 0.7
            ORDER BY
              CASE WHEN subjects_csv LIKE '%ryani%' AND subjects_csv LIKE '%leo%' THEN 0 ELSE 1 END,
              CASE WHEN lower(coalesce(composition,'')) IN ('overhead','wide','far') THEN 1 ELSE 0 END,
              captured_iso DESC
            LIMIT 100
            """,
        ).fetchall()
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
            "sub": r["subjects_csv"] or "", "mood": r["mood"] or "",
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
            "sub": r["subjects_csv"] or "", "mood": r["mood"] or "",
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            return json.loads(match.group(0))
        raise RuntimeError(f"No valid JSON array in LLM response (len={len(text)}): {text[:200]}")


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

    if style_filter == "real_footage":
        return _propose_realfootage_singlepass(target, context, progress_cb)

    if os.getenv("USE_WRITER_DIRECTOR", "1") == "0":
        return _propose_concepts_legacy(target, context, style_filter)

    # PD 2026-06-06: feed the UNIFIED arc (av+rf share one series) into the
    # ai_vtuber writer too — series-so-far + showrunner directive (rolling
    # ~1-month season plan w/ season·holiday·trend·monthly re-intro·fantasy).
    try:
        from agents import arc as _arc
        context["series_so_far"] = _arc.series_so_far(_db(), n=10)
        context["arc_directive"] = _arc.next_directive(
            _db(), today=target.isoformat(), render_style="ai_vtuber")
    except Exception as e:
        log.warning("arc directive (av) failed: %s", e)

    try:
        from agents.writer_director import propose_concepts_v2
        # PD 2026-06-10 (b): drop concepts with NO cuts before they reach the Validator.
        # A degraded LLM (only Anthropic up) sometimes returns an empty "shell" concept;
        # the Validator then blocks it "fatally incomplete — no cuts" and the slot
        # re-proposes → wasteful churn. Filter empties; if ALL are empty, retry ONCE,
        # then return only concepts that actually have cuts (else empty → slot shows
        # '컨셉 없음' cleanly, no churn loop).
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
        log.warning("writer_director failed (%s) — falling back to legacy single-pass", e)
        if progress_cb:
            progress_cb(f":warning: Writer+Director 실패 ({str(e)[:80]}) — legacy로 fallback")
        return _propose_concepts_legacy(target, context, style_filter)


REALFOOTAGE_SINGLEPASS_PROMPT = ROOT / "agents" / "prompts" / "realfootage_concept.md"

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
                aid = c.get("asset_id")
                if aid:
                    used.add(aid)
    except Exception as e:
        log.warning("cooldown lookup failed: %s", e)
    return used


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
    """PD 2026-06-12: one-take is an OCCASIONAL editing option, not the RF default.
    Deterministic per-day gate — ~1 in RF_ONETAKE_EVERY days (default 3) is a one-take
    day (stable for a given date so re-proposes agree)."""
    import hashlib as _hl
    every = max(1, int(os.getenv("RF_ONETAKE_EVERY", "3")))
    h = int(_hl.sha1(target.isoformat().encode()).hexdigest(), 16)
    return (h % every) == 0


def _rf_long_candidates(context: dict) -> list[dict]:
    """The 12s+ original clips available to RF, longest-first (id/dur/sc/date).
    PD 2026-06-12: honor the BATCH dedup (context['exclude_asset_ids'], per slot) so
    two same-day slots don't grab the identical clip+segment. But do NOT apply the
    cross-day upload cooldown here — a long clip has lots of unused footage and CAN be
    reused another day with a DIFFERENT segment ("같은 동영상이더라도 사용하지 않은 구간은
    향후 사용해도 되잖아"); the one-take picks a varied trim_start so re-use differs."""
    _min = float(os.getenv("RF_ONETAKE_MIN_SEC", "12"))
    _excl = set(context.get("exclude_asset_ids") or [])
    pool = (context.get("available_videos") or []) + (context.get("archive_videos") or [])
    longs = [{"id": v.get("id"), "dur": float(v.get("dur") or 0),
              "sc": (v.get("sc") or "")[:240], "date": v.get("date")}
             for v in pool
             if isinstance(v.get("dur"), (int, float)) and v.get("dur") >= _min
             and v.get("id") not in _excl]
    longs.sort(key=lambda v: -(v.get("dur") or 0))
    # de-dup by id
    seen, out = set(), []
    for v in longs:
        if v["id"] and v["id"] not in seen:
            seen.add(v["id"]); out.append(v)
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
        "\"ko\":\"\",\"en\":\"\"}]}.")
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


def _propose_realfootage_singlepass(target: dt.date, context: dict,
                                     progress_cb: ProgressCb = None,
                                     prior_feedback: str = "") -> list[dict]:
    """PD 2026-06-04: dedicated lean real_footage storyteller. ONE LLM call
    that reads the clip ground truth and writes a flowing narrative grounded
    in what the clips actually show (쿠들습격 style, but honest).

    PD 2026-06-06: `prior_feedback` carries the Giri review's findings from a
    failed attempt so this re-proposal fixes them (the Giri-driven retry loop).
    """
    # PD 2026-06-12: one-take is ONE editing OPTION, not the RF default — "스크립트에
    # 따라 그런 날도 있다는 거지. RF 모두 다 원테이크를 쓰라는 의미가 아냐." So use it only
    # OCCASIONALLY (deterministic ~1 in RF_ONETAKE_EVERY days) when a strong long clip
    # exists; most days go through the normal montage writer. RF_FORCE_ONETAKE=1 forces
    # it (testing); =never disables.
    _ot_mode = os.getenv("RF_FORCE_ONETAKE", "auto")
    if _ot_mode != "never" and (_ot_mode == "1" or _onetake_today(target)):
        _longs = _rf_long_candidates(context)
        if _longs:
            try:
                ot = _propose_realfootage_onetake(
                    target, context, _longs[0], progress_cb, prior_feedback)
                if ot:
                    return ot
            except Exception as e:
                log.warning("one-take path failed (%s) — falling back to montage", e)
    if progress_cb:
        msg = ":pencil: real_footage 단일-패스 스토리텔러 (grounded flowing)"
        if prior_feedback:
            msg += " — 기리 피드백 반영 재작성"
        progress_cb(msg)
    system = REALFOOTAGE_SINGLEPASS_PROMPT.read_text(encoding="utf-8")
    # Feed both videos (Tier 1) and photos (Tier 2). PD 2026-06-06: photos are
    # NOT dropped anymore — every photo cut is animated via Seedance photo_i2v
    # so the writer can use a photo for the payoff/closer and still get motion.
    # The writer must mark photo cuts with source_hint="photo_i2v" + a
    # motion_prompt grounded in the photo.
    # PD 2026-06-06: exclude clips used in the last N real_footage episodes so
    # the same footage isn't reused back-to-back (4-episode cooldown).
    avail_videos = context.get("available_videos", [])
    # PD 2026-06-11: MERGE old/archive footage into the main candidate pool. It used
    # to be a separate "memory-lane only" field, so RF kept re-using the same recent
    # ~28 clips (med_2026_05_25_144138 appeared in ALL 4 last episodes = "재탕") and
    # NEVER touched the years of older footage (2015/16 baby Ryani etc. — perfect for
    # the Ryani-intro). Merging gives a much bigger pool, so the cooldown can exclude
    # reused clips without starving (no more "relax → reuse"), and old footage becomes
    # a first-class pick. years_ago is already stamped for time-grounded captions.
    _arch = context.get("archive_videos", [])
    _seen = {v.get("id") for v in avail_videos if v.get("id")}
    avail_videos = avail_videos + [v for v in _arch if v.get("id") and v.get("id") not in _seen]
    try:
        _con = _db()
        cooldown = _recently_used_rf_assets(_con)
        before = len(avail_videos)
        filtered = [v for v in avail_videos if v.get("id") not in cooldown]
        # Safety: don't starve the writer. If the cooldown leaves too few clips
        # for a full episode, relax it (still prefer fresh, but allow reuse).
        if len(filtered) >= 6:
            avail_videos = filtered
            if cooldown and progress_cb:
                progress_cb(f":snowflake: 최근 {RF_CLIP_COOLDOWN_EPISODES}편 사용 클립 "
                            f"{before - len(filtered)}개 제외 (쿨다운)")
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
    _photos = list(context.get("available_photos", []))
    _pseen = {p.get("id") for p in _photos if p.get("id")}
    _photos += [p for p in context.get("archive_photos", [])
                if p.get("id") and p.get("id") not in _pseen]
    # PD 2026-06-11: the exclude (batch-dedup) MUST also cover the PHOTO pool and
    # the separately-passed archive_videos field — the 6/12 RF 18:00 reused two
    # RF 08:00 clips because they re-entered as photo / raw-archive candidates that
    # the video-only filter above never touched (재탕 누수). Apply _excl to every
    # pool the writer can pick from, but never starve it (keep ≥6).
    if _excl:
        _pk = [p for p in _photos
               if p.get("id") not in _excl and p.get("asset_id") not in _excl]
        if len(_pk) >= 6:
            _photos = _pk
    _arch_field = [v for v in context.get("archive_videos", [])
                   if not _excl or (v.get("id") not in _excl
                                    and v.get("asset_id") not in _excl)]
    # PD 2026-06-11: RF default = long-original ONE-TAKE. The writer kept ignoring
    # the prompt rule and montaging 6-9s trims even when 38s/75s clips sat in the
    # pool — a "label not rule" miss. So PRE-COMPUTE the long candidates and inject
    # them explicitly + sorted (longest first) so they can't be missed; the prompt
    # treats a non-empty list as a near-mandate to build a 1-2 cut one-take.
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
        "available_photos": _photos[:100],
        # PD 2026-06-07: archive (older) clips for past⇄present memory-lane /
        # character-intro episodes. Each has years_ago — if you use one, the
        # caption MUST state the time point ("○년 전", "입양 첫날", "그때는…").
        "archive_videos": _arch_field,
        "archive_year_summary": context.get("archive_year_summary", {}),
        "video_date_clusters": context.get("video_date_summary", {}),
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
    try:
        from agents import arc as _arc
        _series = _arc.series_so_far(_db(), n=10)
        _dir = _arc.next_directive(_db(), today=target.isoformat(),
                                   render_style="real_footage")
        if _series or _dir:
            user += "\n\n" + _series
            if _dir:
                user += ("\n\n## 오늘의 showrunner 디렉티브 (시즌 플랜 기반):\n" + _dir)
            user += ("\n위 시리즈/디렉티브를 이어받아라: 이미 한 소개/스토리 반복 금지, "
                     "열린 떡밥은 잇거나 회수, 이번 회차의 시리즈상 진전을 의식. "
                     "단 자산에 실제 있는 것만 — 디렉티브가 자산과 안 맞으면 자산 우선.")
    except Exception as e:
        log.warning("arc directive injection failed: %s", e)
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
        # PD 2026-06-08: do NOT force finale=video. A photo_i2v finale / Ryani
        # zoom is fine WHEN the quality (marking accuracy) is good — the quality
        # gate decides, not a blanket rule. (Earlier blanket auto-swap removed.)
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
            new_concepts = _propose_realfootage_singlepass(
                target, context, progress_cb, prior_feedback=feedback)
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
