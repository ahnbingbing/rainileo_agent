"""
agents/set_knowledge_builder.py — Phase A.

Aggregates VLM-analyzed photos+videos per set_anchor into structured
"set knowledge": persistent backgrounds, recurring objects, typical actions
(Ryani/Leo individually + together), era-specific changes.

Output: enriched set_library.json (in-place) — each set_anchor entry gets
new fields `persistent_background`, `recurring_items`, `typical_actions`,
`era_changes`, `notable_details`.

Sibling output: rows in `set_objects` DB table for the recurring items
(Phase B uses this).

Run:
    python -m agents.set_knowledge_builder            # build all sets
    python -m agents.set_knowledge_builder --set home_pet_feeding_area
    python -m agents.set_knowledge_builder --dry-run  # print plan only
    python -m agents.set_knowledge_builder --force    # rebuild even if cached

Cost: ~$0.30-0.80 per set on Opus 4.7. 10 sets ≈ $5-8.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("set_knowledge_builder")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
SET_LIBRARY_PATH = ROOT / "data" / "set_library.json"
MODEL = os.getenv("WRITER_MODEL", "claude-opus-4-7")

# How many photo+video samples to feed per set. Opus context is large but we
# cap to keep cost predictable. Bumped 60→90 to leave room for recent-weighted
# sampling (60% recent / 40% older era-spread).
MAX_SAMPLES_PER_SET = 90
MAX_VIDEO_SAMPLES = 24

# Recency policy — 2026-05-31 update at PD request "최근 영상들을 기준으로 재보정".
# Anything captured on or after this ISO date counts as "recent" and is
# prioritized in sampling + prompted to Opus as the authoritative current
# state of the set. Older samples still feed era_changes detection.
RECENT_CUTOFF_ISO = "2025-10-01"
RECENT_QUOTA_FRACTION = 0.6  # 60% of MAX_SAMPLES_PER_SET reserved for recent

SYSTEM_PROMPT = """\
You are the "Set Knowledge Synthesizer" for the Ryani & Leo YouTube Shorts
channel. Given a corpus of VLM-analyzed photo/video samples taken at one
specific location (a "set_anchor"), distill structured knowledge that the
Writer and Director agents can later use to generate accurate concepts.

**Recency rule (CRITICAL):** Samples include both photos and videos. Each
sample is tagged `[RECENT]` (captured 2025-10-01 onwards) or `[OLDER]`.
**RECENT samples represent the CURRENT state of the set** — they are
authoritative for `persistent_background`, `recurring_items` frequency
classifications, `notable_details`, and the present layout. OLDER samples
are only useful for `era_changes` (what was different) and for cross-checking
anti_stereotypes. If a feature appears ONLY in older samples and never in
recent ones, mark it as past-era in `era_changes`, NOT current recurring.

Your output is a single JSON object. Be specific. Avoid generic stereotypes —
ground every claim in concrete signals from the provided samples.

Required output schema:
{
  "set_anchor": string,                  // echo the input
  "persistent_background": {
    "summary": string,                   // 1-2 sentence overview
    "wall_treatment": string,            // e.g. "off-white textured wallpaper", or null
    "floor_type": string,                // e.g. "beige laminate wood", or null
    "main_furniture": [string],          // items present in many samples
    "window_or_light": string            // window position + natural-light character
  },
  "recurring_items": [
    {
      "name_ko": string,                 // e.g. "파란 사료 받침대"
      "description": string,             // visual description for prompt embedding
      "frequency": "always"|"often"|"sometimes",
      "era": string|null,                // date-range if changed over time, else null
      "category": "furniture"|"food"|"toy"|"vessel"|"accessory"|"decor"
    }
  ],
  "typical_actions": {
    "ryani": [string],                   // what Ryani typically does here
    "leo": [string],                     // what Leo typically does here
    "interactions": [string],            // what they do TOGETHER here
    "human_involvement": [string]        // grandma's hand role etc.
  },
  "era_changes": [
    { "era": string, "change": string }  // e.g. "until 2025-10" / "from 2025-11"
  ],
  "notable_details": [string],           // anything visually distinctive worth preserving
  "anti_stereotypes": [string]           // common AI assumptions that DON'T apply (e.g. "할머니집은 어수선" → 실제 깨끗)
}

Be honest. If you don't have evidence for a field, set it to null or an empty
array. Don't fabricate. Korean text is fine in any string field.
"""

USER_TEMPLATE = """\
set_anchor: {set_anchor}
korean_label: {korean}
declared_backgrounds: {backgrounds}
location_type: {location_type}
window_directions: {window_directions}

Below are {n_samples} sample observations (photo/video) from this set, with
date, kind, mood, subjects, background label, and VLM-generated scene
description. Use these to build the set knowledge JSON.

{samples_block}
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def gather_samples(con: sqlite3.Connection, set_anchor: str, lib_entry: dict) -> list[dict]:
    """Recency-weighted sampling — 60% recent (post-RECENT_CUTOFF_ISO) +
    40% older era-spread. Both photos and videos included. PD asked for this
    on 2026-05-31 because legacy era-spread was diluting authoritative recent
    state with outdated decor and gear."""
    loc = lib_entry.get("location_type")
    bgs = lib_entry.get("backgrounds", []) or []
    if not loc:
        return []
    bg_clause = ""
    params: list = [loc]
    if bgs:
        bg_marks = ",".join(["?"] * len(bgs))
        bg_clause = f"AND background IN ({bg_marks})"
        params.extend(bgs)
    rows = con.execute(
        f"""
        SELECT asset_id, kind, captured_iso, mood, subjects_csv, background,
               activity, scene_description, location_tag, age_tag
        FROM assets
        WHERE vlm_analyzed_at IS NOT NULL
          AND location_type = ?
          {bg_clause}
          AND quality_score >= 0.5
        ORDER BY captured_iso DESC
        """,
        params,
    ).fetchall()
    all_rows = [dict(r) for r in rows]
    if len(all_rows) <= MAX_SAMPLES_PER_SET:
        return all_rows

    import random
    random.seed(42)
    recent_pool = [r for r in all_rows if (r.get("captured_iso") or "") >= RECENT_CUTOFF_ISO]
    older_pool = [r for r in all_rows if (r.get("captured_iso") or "") < RECENT_CUTOFF_ISO]

    recent_quota = int(MAX_SAMPLES_PER_SET * RECENT_QUOTA_FRACTION)
    # If we have fewer recent than quota, take them all + fill from older
    recent_take = min(recent_quota, len(recent_pool))
    older_quota = MAX_SAMPLES_PER_SET - recent_take

    sampled: list[dict] = []
    # Recent: bias toward video + most recent — sort by ISO desc, prefer kind=video
    recent_pool.sort(key=lambda r: (r.get("kind") == "video", r.get("captured_iso") or ""),
                     reverse=True)
    sampled.extend(recent_pool[:recent_take])

    # Older: era-spread (proportional per year) — surface era_changes signal
    if older_pool and older_quota > 0:
        years: dict[str, list[dict]] = {}
        for r in older_pool:
            y = (r.get("captured_iso") or "????")[:4]
            years.setdefault(y, []).append(r)
        per_year = max(2, older_quota // max(len(years), 1))
        for items in years.values():
            sampled.extend(random.sample(items, min(per_year, len(items))))

    if len(sampled) > MAX_SAMPLES_PER_SET:
        # Trim older randomly while keeping all recent
        recent_part = [s for s in sampled
                       if (s.get("captured_iso") or "") >= RECENT_CUTOFF_ISO]
        older_part = [s for s in sampled
                      if (s.get("captured_iso") or "") < RECENT_CUTOFF_ISO]
        keep_older = MAX_SAMPLES_PER_SET - len(recent_part)
        if keep_older > 0 and len(older_part) > keep_older:
            older_part = random.sample(older_part, keep_older)
        sampled = recent_part + older_part
    log.info("recency-weighted: %d recent + %d older = %d samples",
             sum(1 for s in sampled if (s.get("captured_iso") or "") >= RECENT_CUTOFF_ISO),
             sum(1 for s in sampled if (s.get("captured_iso") or "") < RECENT_CUTOFF_ISO),
             len(sampled))
    return sampled


def render_samples_block(samples: list[dict]) -> str:
    """Each sample tagged [RECENT] (>=RECENT_CUTOFF_ISO) or [OLDER] so Opus
    can weight current state vs era_changes signal."""
    lines = []
    for s in samples:
        date = (s.get("captured_iso") or "")[:10]
        recency = "RECENT" if (s.get("captured_iso") or "") >= RECENT_CUTOFF_ISO else "OLDER"
        kind = s.get("kind", "?")
        mood = s.get("mood") or "-"
        subs = s.get("subjects_csv") or "-"
        bg = s.get("background") or "-"
        act = s.get("activity") or "-"
        desc = (s.get("scene_description") or "").replace("\n", " ").strip()
        lines.append(
            f"- [{recency}|{date}|{kind}] subjects={subs} mood={mood} bg={bg} act={act}\n"
            f"    desc: {desc}"
        )
    return "\n".join(lines)


def call_opus(system: str, user: str, max_tokens: int = 12000) -> str:
    """PD 2026-06-02: LLM cascade (OpenAI → Gemini → Anthropic). Function
    name kept for compatibility; no longer directly calls Anthropic."""
    from agents.llm_cascade import call_text_cascade
    return call_text_cascade(system, user, max_tokens=max_tokens,
                                anthropic_model=MODEL)


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


def build_one(con: sqlite3.Connection, set_anchor: str, lib_entry: dict,
              dry_run: bool = False) -> dict | None:
    samples = gather_samples(con, set_anchor, lib_entry)
    if not samples:
        log.warning("no samples for %s — skip", set_anchor)
        return None
    log.info("building knowledge for %s with %d samples", set_anchor, len(samples))
    if dry_run:
        return {"_dry_run": True, "n_samples": len(samples)}

    user = USER_TEMPLATE.format(
        set_anchor=set_anchor,
        korean=lib_entry.get("korean", ""),
        backgrounds=lib_entry.get("backgrounds", []),
        location_type=lib_entry.get("location_type", ""),
        window_directions=lib_entry.get("window_directions", []),
        n_samples=len(samples),
        samples_block=render_samples_block(samples),
    )
    raw = call_opus(SYSTEM_PROMPT, user)
    try:
        knowledge = json.loads(strip_fences(raw))
    except json.JSONDecodeError as e:
        log.error("Opus returned non-JSON for %s: %s\n--- first 500 ---\n%s",
                  set_anchor, e, raw[:500])
        return None
    knowledge["_n_samples"] = len(samples)
    knowledge["_built_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return knowledge


def sets_with_new_photos_since(con: sqlite3.Connection, since_iso: str,
                                library: dict) -> list[str]:
    """Phase D — find sets whose VLM-analyzed photos increased since the given
    timestamp. Compares new photo count per location_type to ALL photos and
    flags sets where any new arrival happened."""
    affected: set[str] = set()
    for set_anchor, info in library.items():
        loc = info.get("location_type")
        bgs = info.get("backgrounds", []) or []
        if not loc:
            continue
        clauses = ["location_type = ?", "vlm_analyzed_at > ?"]
        params: list = [loc, since_iso]
        if bgs:
            bg_marks = ",".join(["?"] * len(bgs))
            clauses.append(f"background IN ({bg_marks})")
            params.extend(bgs)
        row = con.execute(
            f"SELECT COUNT(*) FROM assets WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        if row and row[0] > 0:
            log.info("Phase D: %s has %d new photos since %s",
                     set_anchor, row[0], since_iso)
            affected.add(set_anchor)
    return sorted(affected)


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--set", action="append", default=[], help="set_anchor (repeatable)")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if `_built_at` exists")
    p.add_argument("--since-date",
                   help="Phase D — only rebuild sets with VLM-analyzed photos newer than this ISO date. "
                        "Use this after batch photo ingest. Example: --since-date 2026-05-01")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    library = json.loads(SET_LIBRARY_PATH.read_text(encoding="utf-8"))
    con = _db()

    if args.since_date:
        # Phase D: only rebuild sets with new photos. Implies --force on those.
        affected = sets_with_new_photos_since(con, args.since_date, library)
        if not affected:
            print(f"No sets have new VLM-analyzed photos since {args.since_date}.")
            return 0
        print(f"Phase D — rebuilding {len(affected)} sets with new arrivals: {affected}")
        plan = affected
    else:
        selected = args.set or list(library.keys())
        unknown = [s for s in selected if s not in library]
        if unknown:
            print(f"ERROR: unknown set(s): {unknown}", file=sys.stderr)
            return 2
        plan = []
        for s in selected:
            if not args.force and library[s].get("_built_at"):
                log.info("skip %s (already built — use --force to rebuild)", s)
                continue
            plan.append(s)

    if not plan:
        print("Nothing to build.")
        return 0
    print(f"Plan: build {len(plan)} sets ({plan[:5]}{'...' if len(plan) > 5 else ''})")

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY required", file=sys.stderr)
        return 2

    built_count = 0
    for s in plan:
        try:
            k = build_one(con, s, library[s], dry_run=args.dry_run)
        except Exception as e:
            log.exception("Failed %s", s)
            print(f"  ERROR: {s} → {e}", file=sys.stderr)
            continue
        if not k:
            continue
        if args.dry_run:
            print(f"  [dry] {s}: {k.get('n_samples', 0)} samples available")
            continue
        # Merge into library entry. Preserve original fields; overwrite knowledge ones.
        entry = library[s]
        for key in ("persistent_background", "recurring_items", "typical_actions",
                    "era_changes", "notable_details", "anti_stereotypes",
                    "_n_samples", "_built_at"):
            if key in k:
                entry[key] = k[key]
        library[s] = entry
        built_count += 1
        print(f"  ✓ {s}: built ({k.get('_n_samples', 0)} samples)")

    if not args.dry_run and built_count:
        SET_LIBRARY_PATH.write_text(json.dumps(library, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print(f"\nWrote enriched set_library.json — {built_count} entries updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
