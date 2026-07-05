"""Register Mac-synced iCloud assets into the VM DB (PD 2026-07-06).

The iCloud sync runs on the Mac (osxphotos → Mac DB) and mirrors the FILES to GCS
(icloud.sync `mirror_local`), but the VM production DB never learned about the new asset
ROWS — so freshly-imported/VLM-tagged footage couldn't reach the VM's Writer pool. This
carries the `assets` table rows across via a GCS JSONL snapshot (the files are already in
GCS; the VM fetches them on demand with icloud.gcs.download_to).

  Mac, after the icloud sync:   python -m scripts.ingest_register --export
  VM, on a cron:                python -m scripts.ingest_register --import

`--export` writes ALL asset rows to gs://<bucket>/db_sync/assets.jsonl (a full snapshot —
small, and an upsert import is idempotent, so a missed run self-heals next time).
`--import` upserts every row into the local (VM) DB by asset_id. Never deletes: the Mac is
the source-of-truth superset, VM-only rows are left intact.
"""
import argparse
import json

from agents.producer import _db
from icloud import gcs

BLOB = "db_sync/assets.jsonl"


def _blob():
    from google.cloud import storage
    return storage.Client().bucket(gcs.BUCKET).blob(BLOB)


def _cols(con) -> list[str]:
    return [r[1] for r in con.execute("PRAGMA table_info(assets)")]


def export_() -> None:
    con = _db()
    cols = _cols(con)
    rows = con.execute(f"SELECT {', '.join(cols)} FROM assets").fetchall()
    payload = "\n".join(json.dumps(dict(zip(cols, r)), ensure_ascii=False) for r in rows)
    _blob().upload_from_string(payload, content_type="application/x-ndjson")
    print(f"exported {len(rows)} asset rows → gs://{gcs.BUCKET}/{BLOB}", flush=True)


def import_() -> None:
    con = _db()
    local_cols = set(_cols(con))
    try:
        text = _blob().download_as_text()
    except Exception as e:
        print(f"no snapshot to import ({e})", flush=True)
        return
    n = up = 0
    seen_before = {r[0] for r in con.execute("SELECT asset_id FROM assets")}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        aid = d.get("asset_id")
        if not aid:
            continue
        keys = [c for c in d if c in local_cols]           # tolerate schema drift
        con.execute(
            f"INSERT OR REPLACE INTO assets ({', '.join(keys)}) "
            f"VALUES ({', '.join('?' * len(keys))})", [d[k] for k in keys])
        n += 1
        if aid not in seen_before:
            up += 1
    con.commit()
    print(f"imported {n} rows into VM DB ({up} new, {n - up} updated)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="Mac: snapshot asset rows → GCS")
    g.add_argument("--import", dest="imp", action="store_true", help="VM: upsert rows from GCS")
    a = ap.parse_args()
    if a.export:
        export_()
    else:
        import_()


if __name__ == "__main__":
    main()
