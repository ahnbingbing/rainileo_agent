"""scripts/backfill_actions.py — populate assets.action_moments via the Gemini native-video
action layer (agents/clip_actions.extract → scripts/video_actions.actions).

WHY: clip SELECTION searches the embedding doc (agents/asset_embed) which is blind to 1-2s
actions the frame-sampled VLM never described. This fills the per-clip action timeline so
selection can find a clip by what actually HAPPENS. (Caption grounding reads actions NATIVELY at
render time, so it doesn't need this — but populating here also lets selection use it.)

Mirrors scripts/backfill_durations.py: idempotent (only rows with NULL action_moments), GCS
download if the file isn't local, recent-first, commits periodically, deletes fetched clips
unless --keep. Run this ONCE to seed the library, then on a timer for new clips. Does NOT depend
on the USE_CLIP_ACTIONS runtime flag — it just writes data; that flag gates CONSUMPTION.

  python scripts/backfill_actions.py --since 2026-09-01 --limit 50
  python scripts/backfill_actions.py --limit 200        # whole recent-first backlog in chunks

Cost: ~$0.003-0.01/clip (Gemini 2.5-flash + Files API). GOOGLE_API_KEY required.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.producer import _db
from agents import clip_actions
from icloud import gcs


def _rows(con, since: str | None, limit: int | None):
    q = ("SELECT asset_id, file_path FROM assets "
         "WHERE kind='video' AND action_moments IS NULL AND file_path IS NOT NULL "
         "AND (duration_sec IS NULL OR duration_sec >= 2)")
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
    ap.add_argument("--since", help="only clips captured on/after this ISO date (e.g. 2026-09-01)")
    ap.add_argument("--limit", type=int, help="cap number of clips this run")
    ap.add_argument("--dry-run", action="store_true", help="list what would be analyzed, no fetch/write")
    ap.add_argument("--keep", action="store_true", help="keep downloaded clips (default: delete fetched)")
    a = ap.parse_args()

    con = _db()
    clip_actions.ensure_column(con)
    rows = _rows(con, a.since, a.limit)
    print(f"{len(rows)} video rows with NULL action_moments"
          f"{f' since {a.since}' if a.since else ''}", flush=True)
    if a.dry_run:
        for r in rows[:50]:
            print(f"  would analyze {r[0]}  {r[1]}")
        if len(rows) > 50:
            print(f"  … +{len(rows) - 50} more")
        return

    done = empty = missing = failed = 0
    t0 = time.time()
    for i, (aid, fpath) in enumerate(rows, 1):
        try:
            existed = gcs.local_path(fpath).exists()
            local = str(gcs.local_path(fpath)) if existed else gcs.download_to(fpath)
            if not local or not Path(local).exists():
                missing += 1
                continue
            moments = clip_actions.extract(local)
            # store even an empty list → marks the row processed (won't be retried forever)
            clip_actions.store(con, aid, moments or [])
            if moments:
                done += 1
            else:
                empty += 1
            if not a.keep and not existed:
                try:
                    Path(local).unlink()
                except Exception:
                    pass
        except Exception as e:
            failed += 1
            print(f"  ! {aid}: {e}", flush=True)
        if i % 10 == 0:
            con.commit()
            print(f"  {i}/{len(rows)}  done={done} empty={empty} missing={missing} failed={failed} "
                  f"({(time.time() - t0) / i:.1f}s/clip)", flush=True)
    con.commit()
    print(f"DONE  done={done} empty={empty} missing={missing} failed={failed}  "
          f"({time.time() - t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
