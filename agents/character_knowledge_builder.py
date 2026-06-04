"""
agents/character_knowledge_builder.py — Phase F.

Aggregates VLM-analyzed photos+videos per human character (할머니 / 할아버지 /
이모 / 사촌 언니 / 사람) into structured character knowledge:
recurring outfits, hair patterns, body type, accessories, era changes,
anti_stereotypes.

Mirrors `agents/set_knowledge_builder.py` (Phase A) one-to-one but for human
character appearance instead of room/set knowledge.

Output: enriched `data/character_library.json` (in-place) — each character
entry gets new fields `appearance_summary`, `recurring_outfits`,
`hair_styles`, `body_features`, `era_changes`, `anti_stereotypes`,
`notable_details`.

Sibling: rows in `character_objects` DB table (parallel of `set_objects`,
Phase B). Filled by `scripts/populate_character_objects.py`.

Run:
    python -m agents.character_knowledge_builder
    python -m agents.character_knowledge_builder --character grandma
    python -m agents.character_knowledge_builder --since-date 2026-05-01
    python -m agents.character_knowledge_builder --force
    python -m agents.character_knowledge_builder --dry-run

Cost: ~$0.30-0.80 per character × 5 chars ≈ $2-4 full rebuild.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import random
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("character_knowledge_builder")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
LIB_PATH = ROOT / "data" / "character_library.json"
MODEL = os.getenv("WRITER_MODEL", "claude-opus-4-7")

MAX_SAMPLES_PER_CHAR = 60

SYSTEM_PROMPT = """\
You are the "Character Appearance Synthesizer" for the Ryani & Leo YouTube
Shorts channel. Given a corpus of VLM-analyzed photo/video samples likely
containing one specific human character, distill that character's RECURRING
visual appearance for the Writer and Director agents to use in prompt
generation.

Critical constraint — the channel hides every human's FACE on screen. So:
- DO NOT describe facial features (eyes, nose, mouth, glasses on eyes).
- DO describe everything ELSE: clothing patterns, hair color/length/style,
  body type, height-adjacency cues, hand/wrist features, footwear, posture,
  accessories worn below the chin (necklaces are fine but generic, watches,
  sleeves).
- The character's body WILL appear in frame (torso, arms, legs); only the
  face will be cropped or shot from behind.

Your output is a single JSON object. Be specific. Avoid generic stereotypes
("Korean grandmother in hanbok with bun") UNLESS the samples actually show
that. Ground every claim in concrete signals from the provided samples.

Required output schema:
{
  "character_id": string,                // echo the input
  "appearance_summary": {
    "summary": string,                   // 1-2 sentence overview
    "body_type": string,                 // e.g. "slim, mid-height" — based on context
    "skin_tone": string,                 // e.g. "warm-toned Korean fair", or null
    "estimated_age_range": string        // e.g. "late 60s to early 70s"
  },
  "recurring_outfits": [
    {
      "name_ko": string,                 // e.g. "꽃무늬 카디건"
      "description": string,             // visual description for prompt embedding
      "frequency": "always"|"often"|"sometimes",
      "era": string|null,                // date-range if changed, else null
      "category": "outfit"|"hair"|"accessory"|"footwear"|"body_feature"
    }
  ],
  "hair": {
    "style": string,                     // e.g. "short permed grey-black hair, ear-length"
    "color": string,
    "era_changes": [string]
  },
  "accessories": [string],               // watches/glasses-on-chest/necklaces/aprons etc.
  "notable_details": [string],           // visually distinctive things worth preserving
  "anti_stereotypes": [string],          // common AI assumptions that DON'T apply
  "uncertainty_notes": [string]          // honest gaps (e.g. "few photos from 2024, may have shifted")
}

Be honest. If you don't have evidence for a field, set it to null or an empty
array. Don't fabricate. Korean text is fine in any string field.
"""

USER_TEMPLATE = """\
character_id: {character_id}
korean: {korean}
role: {role}
gender: {gender}
estimated_age_range: {age_range}
primary_location_type: {primary_location_type}
pd_notes:
{pd_notes}

Below are {n_samples} sample observations (photo/video) where this character
is likely present (filtered by location_type + has_human=1 + VLM keyword
hints). Each sample has date, kind, mood, subjects, background, activity,
and the VLM-generated Korean scene description. Use these to build the
character knowledge JSON.

NOTE: VLM identification is imperfect — some samples may show a different
person. Rely on what is RECURRING across many samples, not edge cases. If
multiple distinct individuals appear consistently, note it in
`uncertainty_notes`.

{samples_block}
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _matches_hints(desc: str, hints: list[str], excludes: list[str]) -> bool:
    d = (desc or "")
    if excludes and any(x in d for x in excludes):
        return False
    if not hints:
        return True
    return any(h in d for h in hints)


def gather_samples(con: sqlite3.Connection, char_id: str,
                    lib_entry: dict) -> list[dict]:
    loc = lib_entry.get("primary_location_type")
    hints = lib_entry.get("vlm_keyword_hints", []) or []
    excludes = lib_entry.get("exclude_keywords", []) or []

    where = ["vlm_analyzed_at IS NOT NULL", "has_human = 1",
             "quality_score >= 0.4"]
    params: list = []
    if loc:
        where.append("location_type = ?")
        params.append(loc)
    sql = (
        "SELECT asset_id, kind, captured_iso, mood, subjects_csv, background, "
        "activity, scene_description, location_tag, age_tag "
        "FROM assets WHERE " + " AND ".join(where) +
        " ORDER BY captured_iso DESC"
    )
    rows = con.execute(sql, params).fetchall()
    pool = [dict(r) for r in rows]

    # Keyword filter (post-SQL because we need Korean substring on
    # scene_description; SQLite UTF-8 LIKE is OK but cleaner here).
    if hints or excludes:
        pool = [r for r in pool
                if _matches_hints(r.get("scene_description", ""), hints, excludes)]

    if not pool:
        return []

    # Era-spread subsampling
    if len(pool) > MAX_SAMPLES_PER_CHAR:
        random.seed(42)
        years: dict[str, list[dict]] = {}
        for r in pool:
            y = (r.get("captured_iso") or "????")[:4]
            years.setdefault(y, []).append(r)
        sampled: list[dict] = []
        per_year = max(2, MAX_SAMPLES_PER_CHAR // max(len(years), 1))
        for items in years.values():
            sampled.extend(random.sample(items, min(per_year, len(items))))
        if len(sampled) > MAX_SAMPLES_PER_CHAR:
            sampled = random.sample(sampled, MAX_SAMPLES_PER_CHAR)
        pool = sampled
    return pool


def render_samples_block(samples: list[dict]) -> str:
    lines = []
    for s in samples:
        date = (s.get("captured_iso") or "")[:10]
        kind = s.get("kind", "?")
        mood = s.get("mood") or "-"
        subs = s.get("subjects_csv") or "-"
        bg = s.get("background") or "-"
        act = s.get("activity") or "-"
        desc = (s.get("scene_description") or "").replace("\n", " ").strip()
        lines.append(
            f"- [{date}] kind={kind} subjects={subs} mood={mood} bg={bg} act={act}\n"
            f"    desc: {desc}"
        )
    return "\n".join(lines)


def call_opus(system: str, user: str, max_tokens: int = 12000) -> str:
    """PD 2026-06-02: LLM cascade (OpenAI → Gemini → Anthropic)."""
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


def build_one(con: sqlite3.Connection, char_id: str, lib_entry: dict,
              dry_run: bool = False) -> dict | None:
    samples = gather_samples(con, char_id, lib_entry)
    if not samples:
        log.warning("no samples for %s — skip (may be PD-seeded later)", char_id)
        return None
    log.info("building knowledge for %s with %d samples", char_id, len(samples))
    if dry_run:
        return {"_dry_run": True, "n_samples": len(samples)}

    pd_notes = lib_entry.get("pd_notes") or []
    pd_block = "\n".join(f"- {n}" for n in pd_notes) if pd_notes else "(none yet)"

    user = USER_TEMPLATE.format(
        character_id=char_id,
        korean=lib_entry.get("korean", ""),
        role=lib_entry.get("role", ""),
        gender=lib_entry.get("gender", ""),
        age_range=lib_entry.get("age_range", ""),
        primary_location_type=lib_entry.get("primary_location_type", "(none — mixed)"),
        pd_notes=pd_block,
        n_samples=len(samples),
        samples_block=render_samples_block(samples),
    )
    raw = call_opus(SYSTEM_PROMPT, user)
    try:
        knowledge = json.loads(strip_fences(raw))
    except json.JSONDecodeError as e:
        log.error("Opus returned non-JSON for %s: %s\n--- first 500 ---\n%s",
                  char_id, e, raw[:500])
        return None
    knowledge["_n_samples"] = len(samples)
    knowledge["_built_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return knowledge


def chars_with_new_photos_since(con: sqlite3.Connection, since_iso: str,
                                 library: dict) -> list[str]:
    """Phase D-equivalent for characters."""
    affected: set[str] = set()
    for char_id, info in library.items():
        loc = info.get("primary_location_type")
        clauses = ["has_human = 1", "vlm_analyzed_at > ?"]
        params: list = [since_iso]
        if loc:
            clauses.append("location_type = ?")
            params.append(loc)
        row = con.execute(
            f"SELECT COUNT(*) FROM assets WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        if row and row[0] > 0:
            log.info("Phase D: %s has %d new human-present photos since %s",
                     char_id, row[0], since_iso)
            affected.add(char_id)
    return sorted(affected)


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--character", action="append", default=[],
                   help="character_id (repeatable)")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if `_built_at` exists")
    p.add_argument("--since-date",
                   help="Only rebuild characters whose location has new VLM-analyzed photos newer than this ISO date.")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    library = json.loads(LIB_PATH.read_text(encoding="utf-8"))
    con = _db()

    if args.since_date:
        affected = chars_with_new_photos_since(con, args.since_date, library)
        if not affected:
            print(f"No characters have new VLM-analyzed photos since {args.since_date}.")
            return 0
        print(f"Phase D — rebuilding {len(affected)} characters: {affected}")
        plan = affected
    else:
        selected = args.character or list(library.keys())
        unknown = [c for c in selected if c not in library]
        if unknown:
            print(f"ERROR: unknown character(s): {unknown}", file=sys.stderr)
            return 2
        plan = []
        for c in selected:
            if not args.force and library[c].get("_built_at"):
                log.info("skip %s (already built — use --force to rebuild)", c)
                continue
            plan.append(c)

    if not plan:
        print("Nothing to build.")
        return 0
    print(f"Plan: build {len(plan)} characters ({plan})")

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY required", file=sys.stderr)
        return 2

    built_count = 0
    for c in plan:
        try:
            k = build_one(con, c, library[c], dry_run=args.dry_run)
        except Exception as e:
            log.exception("Failed %s", c)
            print(f"  ERROR: {c} → {e}", file=sys.stderr)
            continue
        if not k:
            continue
        if args.dry_run:
            print(f"  [dry] {c}: {k.get('n_samples', 0)} samples available")
            continue
        entry = library[c]
        for key in ("appearance_summary", "recurring_outfits", "hair",
                    "accessories", "notable_details", "anti_stereotypes",
                    "uncertainty_notes", "_n_samples", "_built_at"):
            if key in k:
                entry[key] = k[key]
        library[c] = entry
        built_count += 1
        print(f"  ✓ {c}: built ({k.get('_n_samples', 0)} samples)")

    if not args.dry_run and built_count:
        LIB_PATH.write_text(json.dumps(library, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"\nWrote enriched character_library.json — {built_count} entries updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
