"""Backfill GPS grounding (gps_lat/lon/alt, geo_place, geo_water) onto already-ingested
iCloud assets (PD 2026-11-25).

New captures get this inline in icloud.sync, but the existing pool was ingested before the
grounding existed — so the Writer had no authoritative location and the VLM's pixel guess
("바닷가" on an inland stream) stood unchallenged. This reads each asset's GPS + Apple
offline reverse-geocode (one PhotosDB load, under the shared osxphotos lock) and fills the
columns. Idempotent: by default only touches rows still missing gps_lat; --all redoes every
row. Run on the Mac (has the Photos library); the VM picks it up via ingest_register export.

    python -m scripts.backfill_geo            # fill missing only
    python -m scripts.backfill_geo --all      # recompute all icloud rows
    python -m scripts.backfill_geo --limit 50 # cap (test)
"""
import argparse
from pathlib import Path

from agents.producer import _db
from agents.geo import ensure_geo_cols, geo_from_photoinfo, altitude_from_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="recompute every icloud row, not just missing")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    con = _db()
    ensure_geo_cols(con)
    where = "source='icloud' AND source_uuid IS NOT NULL"
    if not a.all:
        where += " AND gps_lat IS NULL"
    rows = con.execute(
        f"SELECT asset_id, source_uuid, kind, file_path FROM assets WHERE {where} "
        f"ORDER BY captured_iso DESC" + (f" LIMIT {int(a.limit)}" if a.limit else "")
    ).fetchall()
    if not rows:
        print("nothing to backfill")
        return

    # Group by uuid — a Live Photo contributes two asset rows that share one PhotoInfo.
    by_uuid: dict[str, list] = {}
    for aid, uuid, kind, fp in rows:
        by_uuid.setdefault(uuid, []).append((aid, kind, fp))
    print(f"{len(rows)} assets across {len(by_uuid)} uuids to backfill", flush=True)

    import osxphotos
    from icloud.sync import _osxphotos_lock

    stats = {"updated": 0, "with_gps": 0, "no_gps": 0, "ocean": 0, "inland_water": 0, "none": 0, "missing_photo": 0}
    with _osxphotos_lock():
        pdb = osxphotos.PhotosDB()
        # One bulk lookup for every uuid, then map — far faster than 23k per-uuid queries.
        pmap = {p.uuid: p for p in pdb.photos(uuid=list(by_uuid.keys()))}
        for i, (uuid, assets) in enumerate(by_uuid.items(), 1):
            photo = pmap.get(uuid)
            if photo is None:
                stats["missing_photo"] += 1
                continue
            geo = geo_from_photoinfo(photo)
            for aid, kind, fp in assets:
                if not geo:
                    stats["no_gps"] += 1
                    continue
                alt = None
                if kind == "video" and fp:
                    p = Path(fp)
                    if not p.is_absolute():
                        p = ROOT / p
                    alt = altitude_from_file(p)
                stats["with_gps"] += 1
                stats[geo["water"]] = stats.get(geo["water"], 0) + 1
                if not a.dry_run:
                    con.execute(
                        "UPDATE assets SET gps_lat=?, gps_lon=?, gps_alt=?, geo_place=?, geo_water=? "
                        "WHERE asset_id=?",
                        (geo["lat"], geo["lon"], alt, geo["place"], geo["water"], aid),
                    )
                    stats["updated"] += 1
            if i % 200 == 0:
                con.commit()
                print(f"  {i}/{len(by_uuid)} uuids…", flush=True)
    if not a.dry_run:
        con.commit()
    print("done:", stats, flush=True)


if __name__ == "__main__":
    main()
