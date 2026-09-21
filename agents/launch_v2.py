"""Launch v2 — senior-director batch model (PD 2026-09-21). FLAG-GATED, off by default.

Every batch makes 9 RF (3 senior-director sources × 3 edit grammars) + AV, drained over a
2-day cycle so daily volume is 6 with the RF casting amortised (one source → 3 grammars):

  Day 1 (produce day): senior director → 3 sources → 9 RF + 1 AV.
                       schedule 5 RF + 1 AV(20:00); carry the other 4 RF to Day 2.
  Day 2 (carry day):   schedule the 4 carried RF + 2 AV.
                       the 2 AV re-imagine Day-1's popular video(s) as fantasy (시의성).

6 slots (KST), evening-peak weighted (viewers peak 18–21; 12:30 was our weakest → 13:00):
  08:00 / 09:00 / 13:00 / 18:00 / 20:00 / 21:00   (AV lives at 20:00; Day 2 also 08:00)

LAUNCH_MODEL=v2 turns it on; anything else keeps the live 4-slot pipeline untouched.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent

SLOTS_V2: list[str] = os.getenv("LAUNCH_TIMESLOTS_V2",
                                "08:00,09:00,13:00,18:00,20:00,21:00").split(",")
GRAMMARS = ("velocity", "meme", "story")
# Which of the 9 produced RF air on Day 1 (the rest carry to Day 2). 5 today, 4 tomorrow.
DAY1_RF_COUNT = int(os.getenv("V2_DAY1_RF", "5"))
# Day-2 AV feedback: a Day-1 winner is "dominant" when its early views ≥ this × the runner-up.
AV_DOMINANCE_RATIO = float(os.getenv("V2_AV_DOMINANCE", "2.0"))


def enabled() -> bool:
    return os.getenv("LAUNCH_MODEL", "").lower() == "v2"


# ── 2-day cycle ────────────────────────────────────────────────────────────

def is_produce_day(target: dt.date) -> bool:
    """Day 1 (produce 9 RF) vs Day 2 (carry). Even ordinal = produce day; a fixed anchor
    keeps the phase stable across restarts (no Date.now dependence)."""
    anchor = int(os.getenv("V2_CYCLE_ANCHOR_ORDINAL", str(dt.date(2026, 1, 1).toordinal())))
    return (target.toordinal() - anchor) % 2 == 0


def day_plan(target: dt.date) -> dict:
    """Deterministic slot → (lane, role) map for `target`. No LLM/DB — pure structure.

    Day 1: 5 RF (grammar) + 1 AV(20:00).           Day 2: 4 RF (carried) + 2 AV(08:00, 20:00).
    role ∈ {"rf_fresh" (produced today), "rf_carry" (from Day 1), "av", "av_timely"}."""
    produce = is_produce_day(target)
    slots = list(SLOTS_V2)
    plan: dict[str, dict] = {}
    if produce:
        for hh in slots:
            if hh == "20:00":
                plan[hh] = {"lane": "ai_vtuber", "role": "av"}
            else:
                plan[hh] = {"lane": "real_footage", "role": "rf_fresh"}
    else:
        for hh in slots:
            if hh in ("08:00", "20:00"):
                plan[hh] = {"lane": "ai_vtuber", "role": "av_timely"}
            else:
                plan[hh] = {"lane": "real_footage", "role": "rf_carry"}
    return {"target": target.isoformat(), "produce_day": produce, "slots": plan}


# ── source cast → grammar pool ─────────────────────────────────────────────

def _pool_from_cast(cast: list) -> list[dict]:
    """Turn a source bible's cast (asset_ids) into _fresh_pool-shaped rows so the grammar
    Writer casts from EXACTLY this source's footage (keeps the 3 sources' footage distinct)."""
    ids = [c.get("asset_id") for c in (cast or []) if c.get("asset_id")]
    if not ids:
        return []
    db = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    db.row_factory = sqlite3.Row
    q = ("SELECT asset_id, scene_description, activity, subjects_csv, duration_sec, "
         "location_type, quality_score FROM assets WHERE asset_id IN (%s)"
         % ",".join("?" * len(ids)))
    rows = {r["asset_id"]: dict(r) for r in db.execute(q, ids).fetchall()}
    db.close()
    return [rows[i] for i in ids if i in rows]     # preserve the source's cast order


def plan_sources_to_grammar(target: dt.date, progress_cb=None) -> dict:
    """Cheap dry-run of the produce-day core: senior director → 3 sources → each source cast
    into the 3 grammars (cast + copy, NO render). Proves the source→9-video wiring and that
    the 3 sources keep distinct footage. Returns {sources, episodes:[{source,grammar,clips,...}]}."""
    from agents import senior_director
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    sources = senior_director.propose_sources(target, progress_cb=progress_cb)
    episodes: list[dict] = []
    for s in sources:
        pool = _pool_from_cast(s.get("cast") or [])
        if len(pool) < 4:
            _sp(f":warning: source {s.get('source_id')} 캐스트<4 → 이 소스 스킵")
            continue
        base = propose_grammar_copy("story", pool, slot_hhmm=None)
        shared = base["clips"]
        for g in GRAMMARS:
            copy = (base["copy"] if g == "story"
                    else propose_grammar_copy(g, pool, fixed_clips=shared)["copy"])
            episodes.append({
                "source_id": s.get("source_id"), "origination": s.get("origination"),
                "grammar": g, "title": s.get("title"),
                "clips": list(dict.fromkeys(shared.values())),
                "caption_sample": _first_caption(copy),
            })
        _sp(f":clapper: source {s.get('source_id')} → {len(GRAMMARS)} grammars cast")
    return {"sources": sources, "episodes": episodes}


def _first_caption(copy: dict) -> str:
    caps = copy.get("captions") or []
    if caps:
        return caps[0].get("ko", "") if isinstance(caps[0], dict) else str(caps[0])
    beats = copy.get("beats") or []
    if beats and isinstance(beats[0], dict):
        return beats[0].get("ko") or beats[0].get("text") or ""
    return ""


# ── Day-2 AV timeliness feedback ───────────────────────────────────────────

def day1_winners(target_day2: dt.date) -> dict:
    """Rank Day-1's published videos by early views, decide the Day-2 AV amplification shape.

    Returns {"mode": "dominant"|"spread"|"none", "winners": [row, ...]}:
      dominant → 2 fantasy takes on the single top video (top1 ≥ RATIO × top2).
      spread   → 1 fantasy take each on top-1 and top-2 (views were similar).
      none     → no measurable Day-1 signal → caller falls back to arc-originated AV."""
    day1 = (target_day2 - dt.timedelta(days=1)).isoformat()
    db = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute(
            "SELECT video_id, card_id, lane, timeslot, publish_at, views_48h "
            "FROM video_performance WHERE substr(publish_at,1,10)=? "
            "AND views_48h IS NOT NULL ORDER BY views_48h DESC", (day1,)).fetchall()]
    except Exception as e:  # noqa: BLE001
        log.warning("day1_winners: %s", e)
        rows = []
    finally:
        db.close()
    if not rows:
        return {"mode": "none", "winners": []}
    top1 = rows[0]
    top2 = rows[1] if len(rows) > 1 else None
    v1 = top1["views_48h"] or 0
    v2 = (top2["views_48h"] or 0) if top2 else 0
    if not top2 or v1 >= AV_DOMINANCE_RATIO * max(v2, 1):
        return {"mode": "dominant", "winners": [top1]}
    return {"mode": "spread", "winners": [top1, top2]}


def _cli() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Launch v2 — dry-run plan")
    ap.add_argument("--date", default=None)
    ap.add_argument("--plan-grammar", action="store_true", help="run senior director → 9 grammar cast (dry)")
    args = ap.parse_args()
    target = dt.date.fromisoformat(args.date) if args.date else dt.date.today() + dt.timedelta(days=2)

    dp = day_plan(target)
    print(f"=== day_plan {target} (produce_day={dp['produce_day']}) ===")
    for hh in SLOTS_V2:
        s = dp["slots"][hh]
        print(f"  {hh}  {s['lane']:>12}  {s['role']}")

    if args.plan_grammar:
        print("\n=== senior director → grammar plan (dry, no render) ===")
        res = plan_sources_to_grammar(target, progress_cb=lambda m: print(m))
        eps = res["episodes"]
        print(f"\n{len(eps)} RF episodes from {len(res['sources'])} sources:")
        for e in eps:
            print(f"  [{e['source_id']}/{e['grammar']:8}] clips={len(e['clips'])} "
                  f"cap='{e['caption_sample'][:36]}' — {e['title'][:40]}")
        from agents import footage_diversity
        print(f"\nfootage-diversity across sources: {footage_diversity.violations(res['sources']) or 'NONE ✓'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
