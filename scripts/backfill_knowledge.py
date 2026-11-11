"""scripts/backfill_knowledge.py — populate assets.clip_knowledge via the Gemini native
video+audio understanding layer (agents/clip_actions.extract_knowledge →
scripts/video_actions.clip_knowledge).

WHY: the footage-aware RF redesign grounds ideation/captions in what each clip ACTUALLY shows
(frame-truth subjects, material beats, off-screen audio, pd_notes cross-checked). That richer
per-clip knowledge lives in assets.clip_knowledge and must be built OFFLINE (amortized) before the
consumers (brainstorm/Director/Writer) are flipped on — see
notes/rf_footage_aware_direction_design.md §2.3 / §6 A2.

Mirrors scripts/backfill_actions.py: idempotent (only rows with NULL clip_knowledge), GCS
download if the file isn't local, recent-first (present-day RF benefits soonest), commits
periodically, deletes fetched clips unless --keep. Feeds pd_notes + captured_iso so the extractor
can cross-check the owner's description against the footage and stamp the temporal-grounding date.
Does NOT depend on the USE_CLIP_KNOWLEDGE runtime flag — it just writes data; that flag gates
CONSUMPTION (build-OFF → backfill → smoke → flip).

  python scripts/backfill_knowledge.py --limit 30            # sample backfill (B0.5 smoke prep)
  python scripts/backfill_knowledge.py --since 2026-09-01    # recent-first backlog in chunks
  python scripts/backfill_knowledge.py --limit 30 --dry-run  # list, no fetch/write/cost

Cost: ~$0.01-0.05/clip (Gemini native video+audio + Files API). GOOGLE_API_KEY required. Paid —
confirm the batch size with PD before a large/full backfill (per-clip cost is small but it adds up).
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
    q = ("SELECT asset_id, file_path, pd_notes, captured_iso FROM assets "
         "WHERE kind='video' AND clip_knowledge IS NULL AND file_path IS NOT NULL "
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
    ap.add_argument("--dry-run", action="store_true", help="list what would be analyzed, no fetch/write/cost")
    ap.add_argument("--keep", action="store_true", help="keep downloaded clips (default: delete fetched)")
    a = ap.parse_args()

    con = _db()
    clip_actions.ensure_knowledge_column(con)
    rows = _rows(con, a.since, a.limit)
    print(f"{len(rows)} video rows with NULL clip_knowledge"
          f"{f' since {a.since}' if a.since else ''}", flush=True)
    if a.dry_run:
        for r in rows[:50]:
            print(f"  would analyze {r[0]}  {r[1]}  pd={'y' if r[2] else '-'}")
        if len(rows) > 50:
            print(f"  … +{len(rows) - 50} more")
        return

    done = missing = failed = 0
    t0 = time.time()
    for i, (aid, fpath, pd_notes, captured_iso) in enumerate(rows, 1):
        try:
            existed = gcs.local_path(fpath).exists()
            local = str(gcs.local_path(fpath)) if existed else gcs.download_to(fpath)
            if not local or not Path(local).exists():
                missing += 1
                continue
            know = clip_actions.extract_knowledge(
                local, pd_notes=pd_notes or None, captured_iso=captured_iso or None)
            if know and isinstance(know, dict):
                clip_actions.store_knowledge(con, aid, know)
                done += 1
            else:
                failed += 1
                print(f"  ! {aid}: empty/invalid knowledge", flush=True)
            if not a.keep and not existed:
                try:
                    Path(local).unlink()
                except Exception:
                    pass
        except Exception as e:
            failed += 1
            print(f"  ! {aid}: {e}", flush=True)
        if i % 5 == 0:
            con.commit()
            print(f"  {i}/{len(rows)}  done={done} missing={missing} failed={failed} "
                  f"({(time.time() - t0) / i:.1f}s/clip)", flush=True)
    con.commit()
    print(f"DONE  done={done} missing={missing} failed={failed}  "
          f"({time.time() - t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
