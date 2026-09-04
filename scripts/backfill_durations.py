"""Backfill NULL `assets.duration_sec` for video clips via ffprobe (PD 2026-09-04).

ROOT: iCloud/Slack ingestion stored `duration_sec` from osxphotos `.duration`, which is
absent for ~89% of clips (2500/2808 NULL), and Live-Photo pairs were hardcoded NULL. The
RF one-take / long-clip candidate filters require `dur>=12`, so every NULL clip — including
fresh 16~109s footage — was INVISIBLE to the writer. The pool looked "small" and slots
emptied while long, usable clips sat unqueried. `icloud/sync.py` now probes at ingest
(going forward); this one-shot clears the existing backlog.

Runs on the MAC (the asset-row source of truth). `assets` rows propagate to the VM via
`scripts.ingest_register --export/--import`, so backfilling here + exporting fills the VM
pool too. (import_ now COALESCEs duration so a later NULL export can't re-blind it.)

  # recent (post-Leo) clips first — what present-day RF actually needs:
  .venv/bin/python -m scripts.backfill_durations --since 2025-09-01
  # whole backlog:
  .venv/bin/python -m scripts.backfill_durations
  # preview only:
  .venv/bin/python -m scripts.backfill_durations --dry-run --limit 20

Downloads each clip from GCS to probe, then deletes any file it fetched (Mac is dev; the
VM renders), so it won't bloat local disk. `--keep` retains them. Idempotent: only touches
rows where duration_sec IS NULL / <= 0, so a re-run resumes where it left off.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from agents.producer import _db
from icloud import gcs
from icloud.sync import _probe_duration


def _rows(con, since: str | None, limit: int | None):
    q = ("SELECT asset_id, file_path FROM assets "
         "WHERE kind='video' AND (duration_sec IS NULL OR duration_sec <= 0) "
         "AND file_path IS NOT NULL")
    args: list = []
    if since:
        q += " AND captured_iso >= ?"
        args.append(since)
    q += " ORDER BY captured_iso DESC"   # recent-first: present-day RF benefits soonest
    if limit:
        q += " LIMIT ?"
        args.append(int(limit))
    return con.execute(q, args).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only clips captured on/after this ISO date (e.g. 2025-09-01)")
    ap.add_argument("--limit", type=int, help="cap number of clips this run")
    ap.add_argument("--dry-run", action="store_true", help="list what would be probed, no fetch/write")
    ap.add_argument("--keep", action="store_true", help="keep downloaded clips (default: delete fetched)")
    a = ap.parse_args()

    con = _db()
    rows = _rows(con, a.since, a.limit)
    print(f"{len(rows)} video rows with NULL/<=0 duration"
          f"{f' since {a.since}' if a.since else ''}", flush=True)
    if a.dry_run:
        for r in rows[:50]:
            print(f"  would probe {r[0]}  {r[1]}")
        if len(rows) > 50:
            print(f"  … +{len(rows) - 50} more")
        return

    filled = missing = failed = 0
    t0 = time.time()
    for i, (aid, fpath) in enumerate(rows, 1):
        try:
            existed = gcs.local_path(fpath).exists()
            local = str(gcs.local_path(fpath)) if existed else gcs.download_to(fpath)
            if not local or not Path(local).exists():
                missing += 1
                continue
            dur = _probe_duration(local)
            if dur and dur > 0:
                con.execute(
                    "UPDATE assets SET duration_sec=? WHERE asset_id=? AND "
                    "(duration_sec IS NULL OR duration_sec <= 0)", (dur, aid))
                filled += 1
            else:
                failed += 1
            if not a.keep and not existed:
                try:
                    Path(local).unlink()
                except Exception:
                    pass
        except Exception as e:
            failed += 1
            print(f"  ! {aid}: {e}", flush=True)
        if i % 25 == 0:
            con.commit()
            print(f"  {i}/{len(rows)}  filled={filled} missing={missing} failed={failed} "
                  f"({(time.time() - t0) / i:.1f}s/clip)", flush=True)
    con.commit()
    print(f"DONE  filled={filled} missing={missing} failed={failed} of {len(rows)}", flush=True)
    if filled:
        print("→ run `python -m scripts.ingest_register --export` to propagate to the VM DB.",
              flush=True)


if __name__ == "__main__":
    main()
