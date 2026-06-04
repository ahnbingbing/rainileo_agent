"""
scripts/populate_character_objects.py — Phase G populator.

Reads enriched character_library.json (produced by
agents.character_knowledge_builder) and inserts each entry's
`recurring_outfits[]` (and any other list-shaped appearance fields)
into the character_objects DB table.

Idempotent on (character_id, name_ko) — re-running upserts the latest
description/frequency/era from the library. Manual PD edits
(source=pd_added / pd_confirmed) are preserved unless --force-overwrite.

Run:
    python3 scripts/populate_character_objects.py
    python3 scripts/populate_character_objects.py --character grandma
    python3 scripts/populate_character_objects.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("populate_character_objects")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
LIB_PATH = ROOT / "data" / "character_library.json"

VALID_CATEGORIES = {"outfit", "hair", "accessory", "footwear", "body_feature"}
VALID_FREQUENCIES = {"always", "often", "sometimes"}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def upsert_one(con: sqlite3.Connection, char_id: str, item: dict,
               default_category: str = "outfit",
               force_overwrite: bool = False, dry_run: bool = False) -> str:
    name_ko = (item.get("name_ko") or "").strip()
    if not name_ko:
        return "skip:empty_name"
    desc = (item.get("description") or "").strip()
    cat = item.get("category") or default_category
    if cat not in VALID_CATEGORIES:
        cat = "outfit"
    freq = item.get("frequency") or "often"
    if freq not in VALID_FREQUENCIES:
        freq = "often"
    era = item.get("era") or None

    existing = con.execute(
        "SELECT id, source FROM character_objects WHERE character_id=? AND name_ko=?",
        (char_id, name_ko),
    ).fetchone()

    if existing and existing["source"] in ("pd_added", "pd_confirmed") and not force_overwrite:
        return "skip:pd_curated"

    if dry_run:
        return "would_upsert" if not existing else "would_update"

    if existing:
        con.execute(
            "UPDATE character_objects SET description=?, category=?, frequency=?, era=?, "
            "source='phase_f_auto', updated_at=datetime('now') WHERE id=?",
            (desc, cat, freq, era, existing["id"]),
        )
        return "updated"
    con.execute(
        "INSERT INTO character_objects (character_id, name_ko, description, category, frequency, era, source) "
        "VALUES (?, ?, ?, ?, ?, ?, 'phase_f_auto')",
        (char_id, name_ko, desc, cat, freq, era),
    )
    return "inserted"


def main() -> int:
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--character", action="append", default=[])
    p.add_argument("--force-overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    library = json.loads(LIB_PATH.read_text(encoding="utf-8"))
    selected = args.character or list(library.keys())

    con = _db()
    summary = {"inserted": 0, "updated": 0, "skipped": 0}
    for c in selected:
        entry = library.get(c)
        if not entry:
            print(f"  ! unknown character {c}", file=sys.stderr)
            continue
        items = entry.get("recurring_outfits") or []
        # Also fold hair entries if present (synthesizer may emit hair separately)
        hair = entry.get("hair") or {}
        if hair.get("style"):
            items = items + [{
                "name_ko": "헤어 스타일",
                "description": f"{hair.get('style')}; color={hair.get('color') or '(미상)'}",
                "category": "hair",
                "frequency": "always",
                "era": None,
            }]
        if not items:
            print(f"  - {c}: 0 recurring items (Phase F not built or empty)")
            continue
        print(f"  → {c}: {len(items)} items")
        for it in items:
            r = upsert_one(con, c, it, force_overwrite=args.force_overwrite,
                           dry_run=args.dry_run)
            if r.startswith("skip"):
                summary["skipped"] += 1
            elif "insert" in r or "would_upsert" in r:
                summary["inserted"] += 1
            elif "updat" in r:
                summary["updated"] += 1
    if not args.dry_run:
        con.commit()
    print(f"\nSummary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
