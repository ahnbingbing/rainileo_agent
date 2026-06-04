"""
scripts/retag_subjects.py — One-shot backfill that re-tags every asset row
using TIME-BASED HARD RULES.

Why this exists
---------------
Photos.app's older machine-vision model frequently misclassified Ryani
(French Bulldog) as a cat. Because the album spans 2015 → 2026, every
asset captured BEFORE Leo joined the household (2025-11-15) is — by
construction — Ryani only, regardless of what labels Photos.app assigned.

Rules applied
-------------
For each row in `assets`:
  1. Pull the photo's osxphotos UUID from the `notes` field
     (sync.py stores it as 'uuid:{uuid}; ...').
  2. Re-fetch labels/persons via osxphotos (read-only).
  3. Decide subjects with this hard time gate:

         captured_iso < '2025-11-15'  (pre-Leo)
             ANY animal label (cat OR dog) -> 'ryani'
             no animal label              -> 'unknown'
             # Leo cannot be in any pre-adoption photo. Period.

         captured_iso >= '2025-11-15'  (post-Leo)
             cat labels  -> add 'leo'
             dog labels  -> add 'ryani'
             both        -> 'leo,ryani'  (stored sorted)
             neither     -> 'unknown'

  4. Recompute age_tag from (subjects, captured_date) using sync.infer_age_tag.

Idempotent — safe to re-run.

Usage
-----
    python -m scripts.retag_subjects --album "Ryani & Leo"            # apply
    python -m scripts.retag_subjects --album "Ryani & Leo" --dry-run  # preview
    python -m scripts.retag_subjects --album "Ryani & Leo" --limit 30 # sample
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

# Reuse the sync module's constants/helpers — single source of truth.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from icloud.sync import (  # noqa: E402
    CAT_LABELS,
    DOG_LABELS,
    DEFAULT_SUBJECT_MAP,
    infer_age_tag,
)

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
LEO_ADOPTED = dt.date(2025, 11, 15)  # The hard gate.

log = logging.getLogger("retag")
UUID_RE = re.compile(r"uuid:([0-9A-Fa-f-]{36})")


def extract_uuid(notes: str | None) -> str | None:
    if not notes:
        return None
    m = UUID_RE.search(notes)
    return m.group(1) if m else None


def _has_cat(labels: set[str]) -> bool:
    lower = {l.lower() for l in labels}
    return bool(labels & CAT_LABELS or lower & {l.lower() for l in CAT_LABELS})


def _has_dog(labels: set[str]) -> bool:
    lower = {l.lower() for l in labels}
    return bool(labels & DOG_LABELS or lower & {l.lower() for l in DOG_LABELS})


def decide_subjects(
    captured_date: dt.date | None,
    labels: list[str],
    persons: list[str],
) -> list[str]:
    """Time-gated hard rule. Returns sorted subject list (possibly empty)."""
    label_set = set(labels or [])
    has_cat = _has_cat(label_set)
    has_dog = _has_dog(label_set)

    # Named-person signal (e.g. user manually tagged faces) — still respected
    # post-adoption only. Pre-adoption, ANY 'leo' name tag is rejected.
    named: set[str] = set()
    for n in persons or []:
        if not n or n == "_UNKNOWN_":
            continue
        sid = DEFAULT_SUBJECT_MAP.get(n)
        if sid:
            named.add(sid)

    pre_leo = captured_date is None or captured_date < LEO_ADOPTED

    if pre_leo:
        # Hard rule: no Leo before adoption, regardless of labels or names.
        if has_cat or has_dog or "ryani" in named:
            return ["ryani"]
        return []  # caller stores '' which we'll write as 'unknown'
    else:
        out: set[str] = set()
        if has_cat:
            out.add("leo")
        if has_dog:
            out.add("ryani")
        out |= named  # post-adoption: trust manual tags too
        return sorted(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retag assets with time-based hard rules.")
    ap.add_argument("--album", default="Ryani & Leo")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would change, don't UPDATE.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N rows (0 = all).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import osxphotos  # type: ignore
    except ImportError:
        log.error("osxphotos not installed. pip install -r requirements.txt")
        return 2

    log.info("opening Photos library (read-only)...")
    pdb = osxphotos.PhotosDB()
    albums = [a for a in pdb.album_info if a.title == args.album]
    if not albums:
        log.error("album not found: %r", args.album)
        return 1

    # Build uuid -> photo map for fast lookup.
    photos_by_uuid: dict[str, object] = {}
    for p in albums[0].photos:
        photos_by_uuid[p.uuid] = p
    log.info("album '%s' has %d photos in Photos.app", args.album, len(photos_by_uuid))

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT asset_id, captured_iso, subjects_csv, age_tag, notes "
        "FROM assets ORDER BY captured_iso"
    ).fetchall()
    log.info("DB has %d asset rows", len(rows))

    # Counters for the report.
    before = Counter()
    after = Counter()
    transitions = Counter()
    unmatched = 0
    updated = 0

    for i, r in enumerate(rows):
        if args.limit and i >= args.limit:
            break

        before[r["subjects_csv"] or "(empty)"] += 1

        uuid = extract_uuid(r["notes"])
        photo = photos_by_uuid.get(uuid) if uuid else None
        if photo is None:
            # Asset row whose source photo was deleted from the album, or
            # whose notes field doesn't carry a uuid (e.g. live-photo pair).
            # Fallback: time-gate only, no labels.
            unmatched += 1
            labels: list[str] = []
            persons: list[str] = []
        else:
            labels = list(getattr(photo, "labels", None) or [])
            persons = list(getattr(photo, "persons", None) or [])

        captured_date: dt.date | None = None
        if r["captured_iso"]:
            try:
                captured_date = dt.date.fromisoformat(r["captured_iso"][:10])
            except ValueError:
                captured_date = None

        new_subjects = decide_subjects(captured_date, labels, persons)
        new_csv = ",".join(new_subjects) if new_subjects else "unknown"
        new_age = infer_age_tag(new_subjects, captured_date)

        old_csv = r["subjects_csv"] or "unknown"
        old_csv = "unknown" if old_csv == "" else old_csv

        if new_csv != old_csv or new_age != r["age_tag"]:
            transitions[(old_csv, new_csv)] += 1
            if not args.dry_run:
                con.execute(
                    "UPDATE assets SET subjects_csv = ?, age_tag = ? WHERE asset_id = ?",
                    (new_csv, new_age, r["asset_id"]),
                )
            updated += 1

        after[new_csv] += 1

    if not args.dry_run:
        con.commit()
    con.close()

    # Report
    print("\n=== retag report ===")
    print(f"  rows scanned:     {sum(before.values())}")
    print(f"  unmatched in app: {unmatched}  (notes had no uuid or photo deleted)")
    print(f"  rows updated:     {updated}")
    print(f"  dry-run:          {args.dry_run}")

    print("\n  before subjects_csv:")
    for k, v in sorted(before.items(), key=lambda kv: -kv[1]):
        print(f"    {v:5d}  {k}")
    print("\n  after subjects_csv:")
    for k, v in sorted(after.items(), key=lambda kv: -kv[1]):
        print(f"    {v:5d}  {k}")

    if transitions:
        print("\n  top transitions (old -> new):")
        for (o, n), v in transitions.most_common(15):
            print(f"    {v:5d}  {o:18s} -> {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
