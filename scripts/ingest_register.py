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
import os

from agents.producer import _db
from icloud import gcs

BLOB = "db_sync/assets.jsonl"

# Columns the VM computes and the Mac export does NOT own — never let an empty/absent export
# value overwrite a real VM-side value (see import_ for why). Add new VM-only columns here.
_VM_OWNED = {"duration_sec", "action_moments", "vis_phash", "has_human"}


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
    blob = _blob()
    # PD 2026-11-23: the snapshot grew past ~60 MB and a single-shot upload_from_string PUT
    # started timing out on the Mac uplink (RetryError: Timeout of 120.0s exceeded), FREEZING
    # the GCS snapshot — and with it the VM Writer pool — at a stale 11-14 state while fresh
    # photos piled up locally. Use a RESUMABLE chunked upload (setting chunk_size switches
    # upload_from_string to resumable) with a generous per-request timeout, so a large/slow
    # upload completes chunk-by-chunk instead of as one fragile all-or-nothing PUT.
    blob.chunk_size = 8 * 1024 * 1024  # 8 MB → resumable upload
    blob.upload_from_string(
        payload, content_type="application/x-ndjson",
        timeout=int(os.getenv("INGEST_EXPORT_TIMEOUT", "600")))
    print(f"exported {len(rows)} asset rows ({len(payload)/1e6:.1f} MB) "
          f"→ gs://{gcs.BUCKET}/{BLOB}", flush=True)


def import_() -> None:
    con = _db()
    # Ensure GPS-grounding columns exist locally so the Mac export (which carries them) is
    # not silently dropped by the local_cols intersection below (PD 2026-11-25).
    from agents.geo import ensure_geo_cols
    ensure_geo_cols(con)
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
        # VM-OWNED columns the Mac export doesn't compute (duration probe, Gemini action
        # layer, perceptual hash, human flag). INSERT OR REPLACE used to re-insert the whole
        # row, NULLing every column the export didn't carry — so each ingest wiped the VM's
        # action_moments backfill (only rows written after the last ingest survived) and would
        # re-blind a probed duration. Fix: (1) the ON CONFLICT upsert below only touches listed
        # keys, so VM-only columns are preserved; (2) drop any VM-owned key whose incoming
        # value is empty, so a NULL/absent export never clobbers a real VM value. Backfill the
        # Mac side (scripts.backfill_*) so exports can carry real values and promote them.
        keys = [c for c in keys if not (c in _VM_OWNED and not d.get(c))]
        set_cols = [c for c in keys if c != "asset_id"]
        if set_cols:
            con.execute(
                f"INSERT INTO assets ({', '.join(keys)}) "
                f"VALUES ({', '.join('?' * len(keys))}) "
                f"ON CONFLICT(asset_id) DO UPDATE SET "
                + ", ".join(f"{c}=excluded.{c}" for c in set_cols),
                [d[k] for k in keys])
        else:
            con.execute("INSERT INTO assets (asset_id) VALUES (?) "
                        "ON CONFLICT(asset_id) DO NOTHING", (aid,))
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
