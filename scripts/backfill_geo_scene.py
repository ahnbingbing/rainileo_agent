"""Scene-model water grounding for GPS-less clips (PD 2026-11-26, Option A + C).

Most of the ~5.8k GPS-less iCloud assets will never get a map location (shared / EXIF
stripped / location-off). But the "첫 바닷가 수영" fabrication only needs ONE thing to be
caught: that an inland/dry clip is NOT the ocean. A Places365 scene model supplies that
cheaply and with ZERO PD questions — used strictly to REFUTE, never to assert:

    clear inland-water scene (swimming_hole/pond/creek/pool/…) → geo_water='inland_water'
    ocean/beach-ish scene                                      → leave NULL (ask PD, never
                                                                 auto-assert the sea)
    no-water scene                                             → leave NULL (a pixel "no
                                                                 water" is too weak to deny)

Every write is stamped geo_source='places365' so a human can audit or bulk-revert the
pixel inferences (they are deliberately weaker than Apple's offline reverse-geocode, which
owns geo_source='apple'). Default is a DRY-RUN report; pass --write to actually update, and
the recurring wrapper additionally requires env GEO_SCENE_ENABLED=1 as a kill-switch.

PD-naming (Option C) propagates a REAL place name a human supplies for a cluster across all
its members (geo_source='pd' — ground truth, the only one who was there):

    python -m scripts.backfill_geo_scene                         # dry-run: what would write
    python -m scripts.backfill_geo_scene --write                 # apply inland_water refute
    python -m scripts.backfill_geo_scene --kind photo --limit 500 --write
    python -m scripts.backfill_geo_scene --name 15 "서울숲 반려견 운동장"   # propagate a name
"""
import argparse
import os
from pathlib import Path

from agents.producer import _db
from agents.geo import ensure_geo_cols, scene_water_class
from scripts.cluster_ungrounded_geo import _frame, OUT_DIR

ROOT = Path(__file__).resolve().parents[1]


def _ungrounded_rows(kind: str, limit: int | None):
    where = "geo_water IS NULL AND source='icloud' AND file_path IS NOT NULL"
    if kind != "all":
        where += f" AND kind='{kind}'"
    q = (f"SELECT asset_id, kind, file_path, duration_sec FROM assets WHERE {where} "
         f"ORDER BY captured_iso DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return _db().execute(q).fetchall()


def backfill(kind: str, limit: int | None, write: bool, fetch: bool = True) -> None:
    con = _db()
    ensure_geo_cols(con)
    rows = _ungrounded_rows(kind, limit)
    print(f"{len(rows)} GPS-less {kind} assets to scene-classify", flush=True)

    frames, meta = [], []
    for aid, k, fp, dur in rows:
        f = _frame(aid, k, fp, dur, fetch=fetch)   # fetch=False → local thumbs only (bounded)
        if f:
            frames.append(f)
            meta.append(aid)
    print(f"have {len(frames)} frames (missing {len(rows) - len(frames)})", flush=True)
    if not frames:
        return

    from scripts import _places365
    _, topk = _places365.predict(frames, topk=3)

    stats = {"inland_water": 0, "ocean_deferred": 0, "no_write": 0}
    writes = []
    for aid, scenes in zip(meta, topk):
        wc = scene_water_class(scenes)
        if wc == "inland_water":
            stats["inland_water"] += 1
            writes.append((aid, scenes[0]))
        elif any(s in _places365_ocean() for s in scenes):
            stats["ocean_deferred"] += 1
        else:
            stats["no_write"] += 1
    print(f"classification: {stats}", flush=True)
    print(f"  → would write inland_water for {len(writes)} clips "
          f"(ocean-ish {stats['ocean_deferred']} deferred to PD)", flush=True)
    for aid, sc in writes[:15]:
        print(f"    {aid}  ({sc})", flush=True)

    if not write:
        print("DRY-RUN — pass --write to apply.", flush=True)
        return
    for aid, _sc in writes:
        con.execute(
            "UPDATE assets SET geo_water='inland_water', geo_source='places365' "
            "WHERE asset_id=? AND geo_water IS NULL", (aid,))
    con.commit()
    print(f"WROTE inland_water+geo_source='places365' for {len(writes)} clips.", flush=True)


def _places365_ocean():
    from agents.geo import _SCENE_OCEAN
    return _SCENE_OCEAN


def propagate_name(cluster_id: str, place: str) -> None:
    """Option C: write a human-confirmed real place name across every member of a cluster."""
    import json
    con = _db()
    ensure_geo_cols(con)
    mpath = OUT_DIR / "manifest_places365_video.json"
    if not mpath.exists():
        raise SystemExit(f"no cluster manifest at {mpath} — run cluster_ungrounded_geo first")
    manifest = json.loads(mpath.read_text())
    cl = manifest.get(str(cluster_id))
    if not cl:
        raise SystemExit(f"cluster {cluster_id} not in manifest (have {list(manifest)[:10]}…)")
    members = cl["members"]
    for aid in members:
        con.execute("UPDATE assets SET geo_place=?, geo_source='pd' WHERE asset_id=?",
                    (place, aid))
    con.commit()
    print(f"propagated geo_place='{place}' (geo_source='pd') to {len(members)} clips "
          f"of cluster {cluster_id} (scene hint was {cl.get('scene_hint')})", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["video", "photo", "all"], default="video")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write", action="store_true", help="apply (default: dry-run report)")
    ap.add_argument("--name", nargs=2, metavar=("CLUSTER_ID", "PLACE"),
                    help="propagate a PD-confirmed place name across a cluster's members")
    ap.add_argument("--no-fetch", action="store_true",
                    help="use only locally-cached thumbnails (no GCS pull) — bounds recurring runs")
    ap.add_argument("--recurring", action="store_true",
                    help="automated context: require env GEO_SCENE_ENABLED=1 to write; implies --no-fetch")
    a = ap.parse_args()

    if a.name:
        propagate_name(a.name[0], a.name[1])
        return
    write = a.write
    fetch = not (a.no_fetch or a.recurring)
    if a.recurring and os.environ.get("GEO_SCENE_ENABLED") != "1":
        print("GEO_SCENE_ENABLED!=1 — recurring scene backfill disabled (kill-switch).", flush=True)
        write = False
    backfill(a.kind, a.limit, write, fetch=fetch)


if __name__ == "__main__":
    main()
