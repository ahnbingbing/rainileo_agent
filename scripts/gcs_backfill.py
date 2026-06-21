"""Phase 3 of the GCS migration (PD 2026-06-21): backfill OFFLOADED originals.

~4,560 assets are iCloud-only (prune removed the local original) and so aren't in
the GCS mirror yet — these are exactly the old memory-lane photos whose dawn
on-demand download keeps failing. This streams them into GCS in bounded batches so
local disk never fills:

    for each batch of N offloaded assets:
        osxphotos bulk-download (one library scan for the whole batch) → staging
        upload each to its GCS blob (path derived from the asset's file_path)
        delete the staging copy            ← keeps disk flat

Run in a HEALTHY (daytime) Photos window. Idempotent + resumable: already-mirrored
assets are skipped, so re-running picks up where it left off.

    .venv/bin/python -m scripts.gcs_backfill            # full backfill
    .venv/bin/python -m scripts.gcs_backfill --batch 60 --limit 120
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from icloud import gcs              # noqa: E402
from icloud.sync import download_assets_by_uuids  # noqa: E402

DB = ROOT / "data" / "agent.db"


def _offloaded() -> list[tuple[str, str, str]]:
    """(asset_id, abs_file_path, source_uuid) for assets that are NOT local and
    NOT already in GCS — the set still needing a backfill."""
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT asset_id, file_path, source_uuid FROM assets "
        "WHERE source_uuid IS NOT NULL AND source_uuid != '' "
        "AND file_path IS NOT NULL AND file_path != ''").fetchall()
    con.close()
    have = gcs.list_blob_names()          # one listing, not thousands of HEADs
    todo = []
    for aid, fp, uuid in rows:
        p = fp if Path(fp).is_absolute() else str(ROOT / fp)
        if Path(p).exists():
            continue                      # local → the mirror script handles it
        if gcs.blob_name(p) in have:
            continue                      # already backfilled
        todo.append((aid, p, uuid))
    return todo


def main() -> int:
    batch = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 80
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    if not gcs.enabled():
        print("GCS disabled"); return 1
    todo = _offloaded()
    if limit:
        todo = todo[:limit]
    total = len(todo)
    print(f"backfill: {total} offloaded assets → GCS (batch={batch})", flush=True)
    staging = ROOT / "data" / "tmp" / "gcs_backfill_staging"
    staging.mkdir(parents=True, exist_ok=True)
    up = miss = 0
    t0 = time.time()
    for i in range(0, total, batch):
        chunk = todo[i:i + batch]
        uuids = [u for _a, _f, u in chunk]
        got = download_assets_by_uuids(uuids, staging, timeout=600.0)
        for aid, fp, uuid in chunk:
            src = got.get(uuid)
            if not src or not Path(src).exists():
                miss += 1
                continue
            # upload to the blob the fetch path expects (derived from file_path)
            tmp_at_fp = Path(fp)
            try:
                tmp_at_fp.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, fp)
                if gcs.upload(fp):
                    up += 1
                else:
                    miss += 1
            except Exception:
                miss += 1
            finally:
                # keep disk flat — this is a re-downloadable original now safe in GCS
                try:
                    if Path(fp).exists():
                        Path(fp).unlink()
                except Exception:
                    pass
        # clear any stragglers in staging
        for f in staging.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        print(f"  {min(i + batch, total)}/{total}  uploaded={up} missing={miss} "
              f"({time.time() - t0:.0f}s)", flush=True)
    print(f"DONE: {up} backfilled, {miss} missing of {total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
