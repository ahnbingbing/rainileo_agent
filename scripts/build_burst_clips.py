"""Convert iOS burst-photo sequences into short smooth video clips + register them as
kind='video' assets so the real_footage pipeline can actually USE them (PD 2026-07-19).

Burst photos (rapid 4–32 frame sequences) sat unused: RF is video-first and treats a
lone photo as a ~0.5s caption-less flash, so a whole burst of young Ryani never became
motion footage. This detects burst clusters (≥MIN_FRAMES photos with ≤MAX_GAP-second
gaps), assembles each into a frame-interpolated (smooth) mp4, mirrors it to GCS, and
inserts an assets row (subjects/date/duration from the source frames). The nightly VLM
tagger then labels them like any other clip, so they flow into RF selection + memory-lane.

  .venv/bin/python -m scripts.build_burst_clips --limit 3        # test a few
  .venv/bin/python -m scripts.build_burst_clips                  # all clusters
  env: BURST_MIN_FRAMES(=4) BURST_MAX_GAP(=2.0) BURST_FPS(=10) BURST_SMOOTH_FPS(=30)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agents.producer import _db          # noqa: E402
from icloud import gcs                    # noqa: E402

MIN_FRAMES = int(os.getenv("BURST_MIN_FRAMES", "4"))
MAX_GAP = float(os.getenv("BURST_MAX_GAP", "2.0"))
FPS = int(os.getenv("BURST_FPS", "10"))
SMOOTH_FPS = int(os.getenv("BURST_SMOOTH_FPS", "30"))
FFMPEG = os.getenv("FFMPEG", "ffmpeg")


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def detect_clusters(con) -> list[list[dict]]:
    rows = con.execute(
        "SELECT asset_id, captured_iso, subjects_csv, file_path FROM assets "
        "WHERE kind='photo' AND captured_iso IS NOT NULL ORDER BY captured_iso, asset_id"
    ).fetchall()
    cols = ["asset_id", "captured_iso", "subjects_csv", "file_path"]
    clusters, cur, prev = [], [], None
    for r in rows:
        d = dict(zip(cols, r))
        t = _ts(d["captured_iso"])
        if t is None:
            continue
        if prev and (t - prev).total_seconds() <= MAX_GAP:
            cur.append(d)
        else:
            if len(cur) >= MIN_FRAMES:
                clusters.append(cur)
            cur = [d]
        prev = t
    if len(cur) >= MIN_FRAMES:
        clusters.append(cur)
    return clusters


def _burst_asset_id(cluster) -> str:
    first = cluster[0]["asset_id"]
    # first frame already encodes YYYY_MM_DD_HHMMSS; reuse it + a stable hash of the members
    stamp = first[len("med_"):].rsplit("_icloud", 1)[0].rsplit("_slack", 1)[0]
    h = hashlib.md5("|".join(c["asset_id"] for c in cluster).encode()).hexdigest()[:8]
    return f"burst_{stamp}_{h}"


def _resolve_frame(fp: str) -> str | None:
    """Local path for a source frame, fetching from GCS if not on disk."""
    if not fp:
        return None
    lp = gcs.local_path(fp)
    if lp.exists() and lp.stat().st_size > 1000:
        return str(lp)
    got = gcs.download_to(fp)
    return got if got and Path(got).exists() and Path(got).stat().st_size > 1000 else None


def build_one(con, cluster, workdir: Path) -> dict | None:
    aid = _burst_asset_id(cluster)
    exists = con.execute("SELECT 1 FROM assets WHERE asset_id=?", (aid,)).fetchone()
    if exists:
        return {"asset_id": aid, "skipped": "already registered"}
    first_iso = cluster[0]["captured_iso"]
    year = str(_ts(first_iso).year)
    rel = f"data/assets/clips/{year}/{aid}.mp4"
    dest = ROOT / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    j = len(cluster)
    if dest.exists() and dest.stat().st_size >= 5000:
        pass  # a prior run already assembled this clip — reuse it, just (re)register
    else:
        seq = workdir / aid
        seq.mkdir(parents=True, exist_ok=True)
        for f in seq.glob("*.jpg"):
            f.unlink()
        j = 0
        for c in cluster:
            path = _resolve_frame(c["file_path"])
            if not path:
                continue
            out = seq / f"s{j:03d}.jpg"
            subprocess.run(
                [FFMPEG, "-y", "-loglevel", "error", "-i", path, "-vf",
                 "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                 "-q:v", "3", str(out)], check=False)
            if out.exists():
                j += 1
        if j < MIN_FRAMES:
            for f in seq.glob("*.jpg"):
                f.unlink()
            seq.rmdir()
            return {"asset_id": aid, "error": f"only {j} frames resolved"}
        # Adaptive base fps so small bursts aren't a sub-second flash and big bursts aren't
        # too long: play ~2s worth, clamped to [3, FPS]. A 4-frame burst → 3fps (1.3s), a
        # 30-frame burst → 10fps (3s, the POC PD liked). Then frame-interpolate to a smooth
        # SMOOTH_FPS (PD pick: variant B).
        base_fps = min(FPS, max(3.0, round(j / 2.0, 1)))
        vf = f"minterpolate=fps={SMOOTH_FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-framerate", str(base_fps),
             "-i", str(seq / "s%03d.jpg"), "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)], check=False)
        for f in seq.glob("*.jpg"):
            f.unlink()
        seq.rmdir()
    if not dest.exists() or dest.stat().st_size < 5000:
        return {"asset_id": aid, "error": "assemble failed"}

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(dest)], capture_output=True, text=True).stdout.strip()
    try:
        dur = round(float(dur), 2)
    except ValueError:
        dur = round(j / FPS, 2)
    gcs.upload(rel)  # mirror to GCS (best-effort; local copy is enough for VM render)

    subj = Counter(c.get("subjects_csv") for c in cluster if c.get("subjects_csv"))
    subjects_csv = subj.most_common(1)[0][0] if subj else None
    con.execute(
        "INSERT INTO assets (asset_id, source, kind, file_path, captured_iso, "
        "ingested_iso, duration_sec, width, height, subjects_csv, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, "archive", "video", rel, first_iso,
         dt.datetime.now(dt.timezone.utc).isoformat(), dur, 720, 1280, subjects_csv,
         f"연사 {len(cluster)}장 합성 (frame-interpolated {SMOOTH_FPS}fps)"))
    con.commit()
    return {"asset_id": aid, "frames": j, "dur": dur, "subjects": subjects_csv,
            "path": rel}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only build the first N new clusters")
    ap.add_argument("--min-frames", type=int, default=MIN_FRAMES)
    a = ap.parse_args()
    con = _db()
    clusters = detect_clusters(con)
    clusters = [c for c in clusters if len(c) >= a.min_frames]
    print(f"detected {len(clusters)} burst clusters (>= {a.min_frames} frames)", flush=True)
    workdir = ROOT / "data" / "tmp" / "burst_build"
    workdir.mkdir(parents=True, exist_ok=True)
    built = skipped = failed = 0
    for i, cluster in enumerate(clusters):
        try:
            res = build_one(con, cluster, workdir)
        except Exception as e:
            res = {"error": str(e)[:120]}
        if res is None:
            continue
        if res.get("skipped"):
            skipped += 1
        elif res.get("error"):
            failed += 1
            print(f"  [{i+1}/{len(clusters)}] FAIL {res.get('asset_id','?')}: {res['error']}", flush=True)
        else:
            built += 1
            print(f"  [{i+1}/{len(clusters)}] built {res['asset_id']} "
                  f"({res['frames']}f {res['dur']}s {res['subjects']})", flush=True)
            if a.limit and built >= a.limit:
                break
    print(f"\nDONE: built {built}, skipped {skipped}, failed {failed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
