"""Mirror local pet assets to the GCS bucket (Phase 1 of the GCS migration, PD
2026-06-21). Threaded, idempotent (skips already-mirrored same-size blobs), logs
progress. Uses icloud.gcs so the blob layout matches the fetch path exactly.

  .venv/bin/python -m scripts.gcs_mirror            # mirror everything local
  .venv/bin/python -m scripts.gcs_mirror --limit 50 # smoke test

gsutil -m rsync deadlocks on macOS (multiprocessing fork bug 33725); this uses the
google-cloud-storage client with a thread pool instead.
"""
from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from icloud import gcs  # noqa: E402

DB = ROOT / "data" / "agent.db"


def _local_assets(limit: int | None) -> list[tuple[str, str]]:
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT asset_id, file_path FROM assets "
        "WHERE file_path IS NOT NULL AND file_path != ''").fetchall()
    con.close()
    out = []
    for aid, fp in rows:
        p = fp if Path(fp).is_absolute() else str(ROOT / fp)
        if Path(p).exists():
            out.append((aid, p))
    return out[:limit] if limit else out


def main() -> int:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not gcs.enabled():
        print("GCS disabled (GCS_ASSETS != 1)"); return 1
    assets = _local_assets(limit)
    total = len(assets)
    print(f"mirroring {total} local assets → gs://{gcs.BUCKET}", flush=True)
    done = ok = fail = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(gcs.upload, fp): aid for aid, fp in assets}
        for fut in as_completed(futs):
            done += 1
            try:
                if fut.result():
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
            if done % 200 == 0 or done == total:
                print(f"  {done}/{total}  ok={ok} fail={fail}", flush=True)
    print(f"DONE: {ok} mirrored, {fail} failed of {total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
