"""
db/init_db.py — Phase 0 SQLite bootstrap

Usage:
    python -m db.init_db                # creates ./data/agent.db, runs schema.sql, seeds milestones
    python -m db.init_db --reset        # drops and recreates the file (DESTROYS DATA)

Reads:
    - db/schema.sql                     (DDL)
    - data/milestones_seed.json         (seed milestones + subjects)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "agent.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
SEED_PATH = ROOT / "data" / "milestones_seed.json"


def load_schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def load_seed() -> dict[str, Any]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "assets").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)


def seed_subjects(con: sqlite3.Connection, seed: dict[str, Any]) -> int:
    rows = []
    for sid, s in seed.get("subjects", {}).items():
        rows.append((
            sid,
            s["species"],
            s["born_iso"],
            1 if s.get("born_is_estimate") else 0,
            s.get("adopted_iso"),
            s.get("$note"),
        ))
    con.executemany(
        """
        INSERT OR REPLACE INTO subjects
            (id, species, born_iso, born_estimate, adopted_iso, notes)
        VALUES (?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def seed_milestones(con: sqlite3.Connection, seed: dict[str, Any]) -> int:
    rows = []
    for m in seed.get("milestones_recurring", []):
        rows.append((
            m["tag"],
            m["month"],
            m["day"],
            m["recurrence"],
            m["memory_lane_default_variant"],
            1 if m.get("imagined_youth_allowed") else 0,
            ",".join(m.get("subjects", [])),
            m.get("notes"),
        ))
    # idempotent: clear existing milestone rows with the same tag, then insert
    tags = [r[0] for r in rows]
    if tags:
        placeholders = ",".join("?" for _ in tags)
        con.execute(f"DELETE FROM milestones WHERE tag IN ({placeholders})", tags)
    con.executemany(
        """
        INSERT INTO milestones
            (tag, month, day, recurrence, memory_lane_default_variant,
             imagined_youth_allowed, subjects_csv, notes)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--reset", action="store_true", help="DELETE existing DB file before init")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()

    if args.reset and db_path.exists():
        db_path.unlink()
        print(f"[reset] removed {db_path}")

    ensure_dirs(db_path)

    if not SEED_PATH.exists():
        print(f"[fatal] seed file not found: {SEED_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    try:
        con.executescript(load_schema())
        seed = load_seed()
        with con:
            n_subjects = seed_subjects(con, seed)
            n_ms = seed_milestones(con, seed)
        n_tables = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        print(f"[ok] db: {db_path}")
        print(f"[ok] tables created: {n_tables}")
        print(f"[ok] subjects seeded: {n_subjects}")
        print(f"[ok] milestones seeded: {n_ms}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
