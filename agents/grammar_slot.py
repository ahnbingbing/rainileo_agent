"""B2/B3 — edit_grammar production wiring (Phase B).

`edit_grammar_for_slot` assigns velocity/meme/story to the day's 3 RF slots (fixed for the
launch month) behind the **EDIT_GRAMMAR_MODE kill-switch** (default off → standard RF). When
on, `produce_grammar_episode` builds a fresh clip pool → the B4 grammar-copy Writer casts +
writes grounded copy → impact_edit renders the grammar edit. The launch slot pipeline calls
this and, on ANY failure, falls back to the standard RF produce (never an empty slot).

Reversibility (the D_lanemix lesson): EDIT_GRAMMAR_MODE=0 (default) reverts every RF slot to
standard trim→burn→assemble on the next batch — no redeploy, no in-flight impact.
"""
from __future__ import annotations
import logging
import os
import sqlite3
import datetime as dt
from pathlib import Path

log = logging.getLogger("agents.grammar_slot")

# Fixed grammar order across the day's RF slots (launch-month; then edit_grammar becomes a
# bandit arm — B5). Index = the slot's position among the day's sorted RF slots.
_GRAMMAR_CYCLE = ["velocity", "meme", "story"]


def edit_grammar_for_slot(target: dt.date, hhmm: str, assignments: list) -> str | None:
    """The grammar assigned to this RF slot, or None (→ standard RF).

    LIVE (PD 2026-09-11): the launch-month grammar test is ON by DEFAULT — the 3 RF slots run
    velocity/meme/story. Reversibility: set env EDIT_GRAMMAR_MODE=0 (instant, next batch) OR
    git-revert this default to "0". (The env-file flip kept hitting tooling friction, so the ON
    switch is git-deployed; the env still overrides for an instant OFF.)
    `assignments` = day_assignments() output [(lane, hhmm), ...]."""
    if os.getenv("EDIT_GRAMMAR_MODE", "1") != "1":
        return None
    rf_slots = sorted(hh for ln, hh in assignments if ln == "real_footage")
    if hhmm not in rf_slots:
        return None
    return _GRAMMAR_CYCLE[rf_slots.index(hhmm) % len(_GRAMMAR_CYCLE)]


def _fresh_pool(exclude: set | None = None, limit: int = 12) -> list[dict]:
    """Recent, unused, clean RF video clips with grounded VLM fields — the candidate pool the
    grammar Writer casts from. Kept simple/coherent: recent first, pets present, decent quality."""
    exclude = exclude or set()
    db = sqlite3.connect(str(Path("data/agent.db")))
    db.row_factory = sqlite3.Row
    used = set(exclude)
    for r in db.execute("SELECT payload_json FROM cards WHERE render_style='real_footage' AND uploaded=1"):
        import json as _j
        try:
            for c in (_j.loads(r[0] or "{}").get("cuts") or []):
                if c.get("asset_id"):
                    used.add(c["asset_id"])
        except Exception:
            pass
    rows = db.execute(
        "SELECT asset_id, scene_description, activity, subjects_csv, duration_sec, "
        "location_type, quality_score FROM assets WHERE kind='video' "
        "AND duration_sec >= 8 AND captured_iso >= '2026-03-01' "
        "AND (quality_score IS NULL OR quality_score >= 0.6) "
        "ORDER BY captured_iso DESC LIMIT 120").fetchall()
    db.close()
    pool = []
    for r in rows:
        aid = r["asset_id"]
        subj = (r["subjects_csv"] or "").lower()
        if aid in used or not ("ryani" in subj or "leo" in subj):
            continue
        pool.append(dict(r))
        if len(pool) >= limit:
            break
    return pool


def _ensure_local(aid: str):
    from icloud import gcs
    db = sqlite3.connect(str(Path("data/agent.db")))
    fp = db.execute("SELECT file_path FROM assets WHERE asset_id=?", (aid,)).fetchone()[0]
    db.close()
    lp = gcs.local_path(fp)
    if not os.path.exists(lp):
        gcs.download_to(fp)


def _title_from_copy(grammar: str, copy: dict) -> str:
    if grammar == "story":
        for b in copy.get("beats", []):
            if b.get("ko"):
                return b["ko"]
    for cp in copy.get("captions", []):
        if cp.get("ko"):
            return cp["ko"]
    return {"velocity": "우리집 국가대표", "meme": "우리집 텐션", "story": "랴니와 레오"}[grammar]


def produce_grammar_episode(grammar: str, target: dt.date, hhmm: str,
                            progress_cb=None, exclude_asset_ids=None):
    """Cast + copy + render one grammar episode. Returns (mp4_path, concept_dict). Raises on any
    failure so the launch caller can fall back to standard RF. NOTE: grammar edits use a different
    aesthetic than the RF Giri rubric, so this path does NOT Giri-gate — the Writer grounds the
    copy and PD spot-checks the batch summary (flag-gated + dry-run-verified before live)."""
    from scripts.impact_edit import render_grammar
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    pool = _fresh_pool(set(exclude_asset_ids or []))
    if len(pool) < 4:
        raise RuntimeError(f"grammar pool too thin ({len(pool)} clips)")
    _sp(f":art: {hhmm} grammar={grammar} — 클립 {len(pool)}개서 캐스팅+카피 생성 중")
    res = propose_grammar_copy(grammar, pool, slot_hhmm=hhmm)
    clips, copy = res["clips"], res["copy"]
    for aid in dict.fromkeys(clips.values()):
        _ensure_local(aid)
    ts = target.strftime("%Y%m%d")
    out = Path(f"data/output/episodes/episode_rf_{grammar}_{ts}_{hhmm.replace(':', '')}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    render_grammar(grammar, out, clips=clips, copy=copy)
    if not out.exists():
        raise RuntimeError("grammar render produced no file")
    title = _title_from_copy(grammar, copy)
    concept = {
        "render_style": "real_footage", "edit_grammar": grammar,
        "title": title, "theme": title, "narrative_oneliner": title,
        "subjects": ["ryani", "leo"],
        "cuts": [{"asset_id": aid} for aid in dict.fromkeys(clips.values())],
    }
    _sp(f":white_check_mark: {hhmm} grammar={grammar} 렌더 완료 → {out.name}")
    return out, concept
