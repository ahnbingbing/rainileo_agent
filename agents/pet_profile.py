"""agents/pet_profile.py — Layer ① of character knowledge (PD 2026-06-07).

Auto-derive each pet's BEHAVIORAL profile by aggregating the VLM tags across all
their clips (activity column + pet_intent/looking_at/micro_behaviors/contextual_
props stored in assets.notes JSON). This is OBSERVED tendency from real footage —
supporting context for the planner/writer, lower authority than PD-confirmed facts
(arc.CHARACTER_FACTS + knowledge.facts_block), which always win on conflict.

Pairs with:
  ② PD facts        — arc.CHARACTER_FACTS / agents/knowledge.py
  ③ ask-when-unknown — agents/knowledge.py

Example: surfaces that Ryani's clips frequently show looking_at=water / play intent,
grounding "물 좋아함" without anyone hand-writing it.
"""
from __future__ import annotations

import collections
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("agents.pet_profile")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"

PETS = {"ryani": ("ryani", "랴니"), "leo": ("leo", "레오")}
_TOP = 6  # top-N per dimension


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def build_profiles(con: sqlite3.Connection | None = None) -> dict:
    own = con is None
    con = con or _db()
    rows = con.execute(
        "SELECT subjects_csv, activity, location_type, notes FROM assets "
        "WHERE vlm_analyzed_at IS NOT NULL AND kind='video'"
    ).fetchall()
    prof = {p: {"n": 0, "activity": collections.Counter(),
                "intent": collections.Counter(),
                "looking_at": collections.Counter(),
                "micro": collections.Counter(),
                "props": collections.Counter(),
                "location": collections.Counter()} for p in PETS}
    for r in rows:
        subj = (r["subjects_csv"] or "").lower()
        try:
            extra = json.loads(r["notes"] or "{}")
        except Exception:
            extra = {}
        for pet, aliases in PETS.items():
            if not any(a in subj for a in aliases):
                continue
            d = prof[pet]
            d["n"] += 1
            if r["activity"]:
                d["activity"][r["activity"]] += 1
            if r["location_type"]:
                d["location"][r["location_type"]] += 1
            if extra.get("pet_intent"):
                d["intent"][extra["pet_intent"]] += 1
            if extra.get("looking_at"):
                d["looking_at"][extra["looking_at"]] += 1
            for m in (extra.get("micro_behaviors") or []):
                d["micro"][m] += 1
            for pr in (extra.get("contextual_props") or []):
                d["props"][pr] += 1
    if own:
        con.close()
    return prof


def _top(counter: collections.Counter, n: int = _TOP) -> str:
    return ", ".join(f"{k}×{v}" for k, v in counter.most_common(n)) or "—"


def profile_block(con: sqlite3.Connection | None = None) -> str:
    """Injectable observed-tendency block (empty if no data). Explicitly marked
    as observed/supporting — PD facts win on conflict."""
    prof = build_profiles(con)
    if not any(prof[p]["n"] for p in prof):
        return ""
    lines = ["## 관찰된 행동 경향 (VLM 클립 집계 — 참고용; PD 사실/학습된 사실이 우선)"]
    for pet, aliases in PETS.items():
        d = prof[pet]
        if not d["n"]:
            continue
        lines.append(
            f"- **{aliases[1]}** (클립 {d['n']}개): "
            f"활동[{_top(d['activity'])}] / 의도[{_top(d['intent'], 4)}] / "
            f"시선[{_top(d['looking_at'], 4)}] / 소품[{_top(d['props'], 4)}]")
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse, logging as _lg
    _lg.basicConfig(level="INFO")
    ap = argparse.ArgumentParser(description="pet behavioral profile (VLM aggregate)")
    ap.add_argument("--block", action="store_true", help="print injectable block")
    args = ap.parse_args()
    con = _db()
    if args.block:
        print(profile_block(con) or "(no data)")
    else:
        prof = build_profiles(con)
        for pet, d in prof.items():
            print(f"\n=== {pet} (n={d['n']}) ===")
            for dim in ("activity", "intent", "looking_at", "micro", "props", "location"):
                print(f"  {dim}: {_top(d[dim])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
