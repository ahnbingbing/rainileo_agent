"""Turn same-session PHOTO bursts into flowing 9:16 video clips + register them as
first-class RF video assets (PD 2026-06-13 idea, wired 2026-09-04).

WHY: ~2000+ post-Leo photos sit in same-session clusters (consecutive shots seconds
apart) that the pipeline only ever used as lone ken-burns stills — a lone photo is
dropped, and a photo is never the closer, so most of this footage was unusable. PD asked
to SEQUENCE consecutive photos into a memory-lane-style video. This clusters them by
time+location, renders each cluster via scripts/photo_sequence (ken-burns + crossfade),
and inserts a synthetic `assets` row (kind='video', real duration, aggregated VLM tags)
so the EXISTING RF video path consumes them — no Writer/Director/cameraman change needed.
(Being pet bursts, they also dodge the bystander-face rule that trips cafe video.)

Runs on the MAC (asset-row source of truth). Renders locally, mirrors the mp4 to GCS, and
inserts the row; `scripts.ingest_register --export` then propagates to the VM pool.

  .venv/bin/python -m scripts.build_photo_sequences --since 2025-09-25            # post-Leo
  .venv/bin/python -m scripts.build_photo_sequences --dry-run --since 2025-09-25  # preview
  .venv/bin/python -m scripts.build_photo_sequences --since 2025-09-25 --limit 5  # first 5

Idempotent: each cluster gets a deterministic asset_id; existing rows are skipped, so a
re-run only builds new clusters. RF_PHOTO_SEQUENCE gating is NOT needed — these are plain
video assets; to withdraw them, veto/delete the seq_* rows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sqlite3
from pathlib import Path

from agents.producer import _db
from icloud import gcs
from icloud.sync import _probe_duration
from scripts.photo_sequence import render as render_sequence

ROOT = Path(__file__).resolve().parent.parent


def _parse_iso(s: str | None):
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", ""))
    except Exception:
        return None


def _clusters(con, since: str | None, gap_sec: int, min_n: int, max_n: int):
    """Group photos into runs of consecutive same-location shots within `gap_sec`."""
    q = ("SELECT asset_id, file_path, captured_iso, subjects_csv, location_type, "
         "location_tag, has_human, source, activity, scene_description, mood "
         "FROM assets WHERE kind='photo' AND captured_iso IS NOT NULL")
    args: list = []
    if since:
        q += " AND captured_iso >= ?"
        args.append(since)
    q += " ORDER BY captured_iso"
    rows = [dict(r) for r in con.execute(q, args).fetchall()]
    runs, cur = [], []
    for r in rows:
        t = _parse_iso(r["captured_iso"])
        if t is None:
            continue
        r["_t"] = t
        if not cur:
            cur = [r]
            continue
        prev = cur[-1]
        same_loc = (r.get("location_type") or "") == (prev.get("location_type") or "")
        near = (t - prev["_t"]).total_seconds() <= gap_sec
        if same_loc and near:
            cur.append(r)
        else:
            if len(cur) >= min_n:
                runs.append(cur)
            cur = [r]
    if len(cur) >= min_n:
        runs.append(cur)
    # a pet must actually be present; drop human-heavy runs (RF excludes those anyway)
    out = []
    for run in runs:
        subs = {s for r in run for s in (r.get("subjects_csv") or "").split(",")
                if s and s != "unknown"}
        if not ({"ryani", "leo"} & subs):
            continue
        # even-sample down to max_n to avoid near-duplicate frames dominating
        if len(run) > max_n:
            step = len(run) / max_n
            run = [run[int(i * step)] for i in range(max_n)]
        out.append(run)
    return out


def _seq_id(run: list[dict]) -> str:
    first = run[0]
    t = first["_t"]
    h = hashlib.sha1("|".join(r["asset_id"] for r in run).encode()).hexdigest()[:8]
    return f"med_{t:%Y_%m_%d_%H%M%S}_seq_{h}"


def _build_one(con, run: list[dict], dry: bool) -> str | None:
    seq_id = _seq_id(run)
    if con.execute("SELECT 1 FROM assets WHERE asset_id=?", (seq_id,)).fetchone():
        return None  # already built
    first = run[0]
    year = f"{first['_t']:%Y}"
    rel = f"data/assets/clips/{year}/{seq_id}.mp4"
    subs = sorted({s for r in run for s in (r.get("subjects_csv") or "").split(",")
                   if s and s != "unknown"})
    n_human = sum(1 for r in run if r.get("has_human"))
    loc_t = first.get("location_type") or ""
    loc_tag = first.get("location_tag")
    if dry:
        print(f"  would build {seq_id}  n={len(run)}  loc={loc_t or '?'}  subj={','.join(subs)}")
        return seq_id
    # fetch constituent photos locally (GCS) and render the sequence
    paths = []
    for r in run:
        p = gcs.local_path(r["file_path"])
        local = str(p) if p.exists() else gcs.download_to(r["file_path"])
        if local and Path(local).exists():
            paths.append(local)
    if len(paths) < 2:
        print(f"  ! {seq_id}: only {len(paths)} photos fetchable, skip")
        return None
    out_abs = ROOT / rel
    ok = render_sequence(paths, out_abs, per=1.7, xfade=0.45, zoom=0.12, max_n=len(paths))
    if not ok or not out_abs.exists():
        print(f"  ! {seq_id}: render failed")
        return None
    dur = _probe_duration(out_abs) or 0.0
    gcs.upload(rel)
    con.execute(
        "INSERT INTO assets (asset_id, source, kind, file_path, captured_iso, duration_sec, "
        "width, height, subjects_csv, location_tag, location_type, has_human, quality_score, "
        "activity, scene_description, mood, composition, vlm_analyzed_at, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (seq_id, "archive", "video", rel, first["captured_iso"], dur, 720, 1280,
         # quality_score 0.65 is DELIBERATELY below the RF pool floor (0.7): ken-burns photo
         # sequences are low-motion and read as "static/photo-like", which drags RF Giri
         # scores (first live attempt scored 4/10 and self-heal had to gut it). So they are
         # BUILT + READY but do NOT auto-enter live batches until PD reviews the style and
         # opts them in — raise this to >=0.7 (or bump SEQ_QUALITY) once approved. This keeps
         # fix-3 wired without letting an unreviewed style flood/starve live RF slots.
         ",".join(subs), loc_tag, loc_t, 1 if n_human * 2 >= len(run) else 0,
         float(os.getenv("SEQ_QUALITY", "0.65")),
         first.get("activity") or "photo_sequence",
         (first.get("scene_description") or "") + " (연속 사진 시퀀스)",
         first.get("mood") or "", "sequence",
         dt.datetime.now().isoformat(timespec="seconds"),
         f"photo_sequence of {len(run)} stills: " + ",".join(r["asset_id"] for r in run)))
    con.commit()
    print(f"  built {seq_id}  {dur:.1f}s  n={len(run)}  loc={loc_t or '?'}  subj={','.join(subs)}")
    return seq_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only photos captured on/after this ISO date")
    ap.add_argument("--gap", type=int, default=90, help="max seconds between consecutive shots in a run")
    ap.add_argument("--min-photos", type=int, default=4)
    ap.add_argument("--max-photos", type=int, default=10)
    ap.add_argument("--limit", type=int, help="cap number of sequences built this run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    con = _db()
    runs = _clusters(con, a.since, a.gap, a.min_photos, a.max_photos)
    print(f"{len(runs)} photo-burst clusters (gap<={a.gap}s, {a.min_photos}-{a.max_photos} photos, pet present)"
          f"{f' since {a.since}' if a.since else ''}", flush=True)
    built = 0
    for run in runs:
        if a.limit and built >= a.limit:
            break
        if _build_one(con, run, a.dry_run):
            built += 1
    print(f"DONE  {'would build' if a.dry_run else 'built'} {built} sequences", flush=True)
    if built and not a.dry_run:
        print("→ run `python -m scripts.ingest_register --export` to propagate to the VM DB.",
              flush=True)


if __name__ == "__main__":
    main()
