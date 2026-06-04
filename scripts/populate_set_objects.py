"""
scripts/populate_set_objects.py — Phase B populator.

Reads enriched set_library.json (produced by agents.set_knowledge_builder)
and inserts each entry's `recurring_items[]` into the set_objects DB table.

Idempotent on (set_anchor, name_ko) — re-running upserts the latest
description/frequency/era from the library. Manual PD edits (source=pd_*)
are preserved unless --force-overwrite is given.

Run:
    python3 scripts/populate_set_objects.py
    python3 scripts/populate_set_objects.py --set home_pet_feeding_area
    python3 scripts/populate_set_objects.py --dry-run
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
log = logging.getLogger("populate_set_objects")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
SET_LIBRARY_PATH = ROOT / "data" / "set_library.json"

VALID_CATEGORIES = {"furniture", "food", "toy", "vessel", "accessory", "decor", "other"}
VALID_FREQUENCIES = {"always", "often", "sometimes"}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def upsert_one(con: sqlite3.Connection, set_anchor: str, item: dict,
               force_overwrite: bool = False, dry_run: bool = False) -> str:
    name_ko = (item.get("name_ko") or "").strip()
    if not name_ko:
        return "skip:empty_name"
    desc = (item.get("description") or "").strip()
    cat = item.get("category") or "other"
    if cat not in VALID_CATEGORIES:
        cat = "other"
    freq = item.get("frequency") or "often"
    if freq not in VALID_FREQUENCIES:
        freq = "often"
    era = item.get("era") or None

    existing = con.execute(
        "SELECT id, source FROM set_objects WHERE set_anchor=? AND name_ko=?",
        (set_anchor, name_ko),
    ).fetchone()

    if existing and existing["source"] in ("pd_added", "pd_edited") and not force_overwrite:
        return "skip:pd_curated"

    if dry_run:
        return "would_upsert" if not existing else "would_update"

    if existing:
        con.execute(
            "UPDATE set_objects SET description=?, category=?, frequency=?, era=?, "
            "source='auto', updated_at=datetime('now') WHERE id=?",
            (desc, cat, freq, era, existing["id"]),
        )
        return "updated"
    con.execute(
        "INSERT INTO set_objects (set_anchor, name_ko, description, category, frequency, era, source) "
        "VALUES (?, ?, ?, ?, ?, ?, 'auto')",
        (set_anchor, name_ko, desc, cat, freq, era),
    )
    return "inserted"


def main() -> int:
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--force-overwrite", action="store_true",
                   help="overwrite even PD-curated rows")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    library = json.loads(SET_LIBRARY_PATH.read_text(encoding="utf-8"))
    selected = args.set or list(library.keys())

    con = _db()
    summary = {"inserted": 0, "updated": 0, "skipped": 0}
    for s in selected:
        entry = library.get(s)
        if not entry:
            print(f"  ! unknown set {s}", file=sys.stderr)
            continue
        items = entry.get("recurring_items") or []
        if not items:
            print(f"  - {s}: 0 recurring_items (Phase A not built or empty)")
            continue
        print(f"  → {s}: {len(items)} items")
        for it in items:
            r = upsert_one(con, s, it, args.force_overwrite, args.dry_run)
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
