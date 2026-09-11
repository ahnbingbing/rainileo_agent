"""B2/B3 — edit_grammar production wiring (Phase B).

`edit_grammar_for_slot` assigns velocity/meme/story to the day's 3 RF slots (fixed for the
launch month) behind the **EDIT_GRAMMAR_MODE kill-switch** (default off → standard RF). When
on, `produce_grammar_episodes_shared` casts ONE clip set and renders all three grammars from
that SAME footage in parallel — a controlled A/B where footage is the control and the edit is
the only variable (the B4 grammar-copy Writer writes grammar-specific grounded copy per arm).
The launch slot pipeline builds this once, then each RF slot pulls its grammar's pre-rendered
mp4; on ANY failure a slot falls back to the standard RF produce (never an empty slot).
(`produce_grammar_episode` — the older per-slot cast+render — is retained for dry-run/standalone.)

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
    """The grammar assigned to this RF slot, or None (→ standard RF). Off unless
    EDIT_GRAMMAR_MODE=1. `assignments` = day_assignments() output [(lane, hhmm), ...]."""
    if os.getenv("EDIT_GRAMMAR_MODE", "0") != "1":
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


def _persist_grammar_card(target: dt.date, concept: dict, out_path) -> str:
    """Register a card row for a rendered grammar episode + link the mp4 via output_video_path.
    Without this the launch scheduler's _auto_upload_episode (which looks a card up BY
    output_video_path) hits [ORPHAN-SKIP] and the slot never schedules — the grammar path
    bypasses produce_and_render, which is what normally creates the card. Returns the card_id."""
    import uuid as _uuid
    import datetime as _dt
    from agents.producer import _db
    from agents.writer import persist_card
    title = concept.get("title") or "랴니와 레오"
    con = _db()
    try:
        card = {
            "card_id": str(_uuid.uuid4()),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "author": "grammar_slot",
            "card_type": "daily",
            "date": target.isoformat(),
            "theme": concept.get("theme") or title,
            "title": title,
            "narrative_oneliner": concept.get("narrative_oneliner") or title,
            "render_style": "real_footage",
            "edit_grammar": concept.get("edit_grammar"),
            "episode_format": "short",
            "subjects": concept.get("subjects", ["ryani", "leo"]),
            "duration_target_sec": 20,
            "writer_confidence": 0.85,
            "ask_pd": False,
            "cuts": concept.get("cuts", []),
            "draft": {"title": title, "description": title,
                      "hashtags": ["#랴니", "#레오", "#일상"], "caption_burnin": title},
        }
        # runs.agent has a CHECK constraint (writer/pd/cameraman/memory/scheduler) — the grammar
        # render is a cameraman-lane render, so log it as 'cameraman'.
        run_cur = con.execute("INSERT INTO runs (agent, status) VALUES ('cameraman', 'ok')")
        con.commit()
        persist_card(con, card, run_cur.lastrowid)
        con.execute("UPDATE cards SET state='approved', output_video_path=?, "
                    "updated_at=datetime('now') WHERE card_id=?",
                    (str(out_path), card["card_id"]))
        con.commit()
        return card["card_id"]
    finally:
        con.close()


def _concept_for(grammar: str, clips: dict, copy: dict) -> dict:
    title = _title_from_copy(grammar, copy)
    return {
        "render_style": "real_footage", "edit_grammar": grammar,
        "title": title, "theme": title, "narrative_oneliner": title,
        "subjects": ["ryani", "leo"],
        "cuts": [{"asset_id": aid} for aid in dict.fromkeys(clips.values())],
    }


def produce_grammar_episodes_shared(grammars: list, target: dt.date, hhmm_by_grammar: dict,
                                    progress_cb=None, exclude_asset_ids=None) -> dict:
    """Controlled edit_grammar A/B: cast ONE clip set, then render every grammar from the SAME
    footage IN PARALLEL — footage is the control, the edit is the only variable. Returns
    {grammar: (mp4_path, concept)}. Raises if the shared cast itself fails (caller falls back to
    standard RF for all slots); a single grammar's render failure just omits that grammar from the
    result (its slot falls back to standard RF). This is why the 3 daily RF slots share footage and
    run together instead of each casting its own clips sequentially."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scripts.impact_edit import render_grammar
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    pool = _fresh_pool(set(exclude_asset_ids or []))
    if len(pool) < 4:
        raise RuntimeError(f"grammar pool too thin ({len(pool)} clips)")
    # Cast ONCE via story — its beat structure references all 5 roles, so it produces the
    # fullest cast for the other grammars to reuse.
    _sp(f":art: RF grammar A/B — 클립 {len(pool)}개서 공유 캐스팅(1회) 중")
    base = propose_grammar_copy("story", pool, slot_hhmm=hhmm_by_grammar.get("story"))
    shared_clips = base["clips"]
    base_copy = base["copy"]
    for aid in dict.fromkeys(shared_clips.values()):
        _ensure_local(aid)
    ts = target.strftime("%Y%m%d")

    def _one(grammar: str):
        hh = hhmm_by_grammar.get(grammar, "")
        copy = (base_copy if grammar == "story"
                else propose_grammar_copy(grammar, pool, slot_hhmm=hh,
                                          fixed_clips=shared_clips)["copy"])
        out = Path(f"data/output/episodes/episode_rf_{grammar}_{ts}_{hh.replace(':', '')}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        render_grammar(grammar, out, clips=shared_clips, copy=copy)
        if not out.exists():
            raise RuntimeError("grammar render produced no file")
        concept = _concept_for(grammar, shared_clips, copy)
        # Register a card + link the mp4 so the launch scheduler can find and schedule it
        # (otherwise [ORPHAN-SKIP] → slot never fills). Non-fatal: a card failure shouldn't
        # discard a good render — the slot would just need a manual schedule.
        try:
            concept["card_id"] = _persist_grammar_card(target, concept, out)
        except Exception as e:
            log.warning("grammar %s card persist failed (slot will orphan): %s", grammar, e)
        _sp(f":white_check_mark: {hh} grammar={grammar} 렌더 완료(공유 footage) → {out.name}")
        return out, concept

    # Cap concurrency: the VM has 2 vCPUs, so 3 simultaneous CPU-bound renders thrash (loadavg ~7,
    # slower wall-clock than 2-wide). 2-wide keeps it parallel without starving. GRAMMAR_RENDER_
    # CONCURRENCY tunes it (a bigger VM can raise it).
    _cc = min(len(grammars), max(1, int(os.getenv("GRAMMAR_RENDER_CONCURRENCY", "2"))))
    results: dict = {}
    with ThreadPoolExecutor(max_workers=_cc) as ex:
        futs = {ex.submit(_one, g): g for g in grammars}
        for fut in as_completed(futs):
            g = futs[fut]
            try:
                results[g] = fut.result()
            except Exception as e:
                log.warning("grammar %s failed in shared render → standard RF: %s", g, e)
                _sp(f":warning: grammar={g} 렌더 실패 → 표준 RF 폴백: {str(e)[:120]}")
    return results
