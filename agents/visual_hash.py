"""Perceptual visual hashing for assets (PD 2026-06-13).

WHY: the existing `assets.phash` column is mislabeled — it holds a 64-hex SHA-256
CONTENT hash (exact-dup only; visually-similar pairs score ~random Hamming) and is
NULL for every video. It is useless for the "how visually similar are two clips"
question PD wants the pool-builder and the Macro Reviewer to answer.

This module computes a REAL perceptual hash (imagehash.phash, 256-bit → 64 hex)
into a SEPARATE column `assets.vis_phash`:
  - PHOTO → one 256-bit hash from the file.
  - VIDEO → a SIGNATURE: several frames sampled across the clip, each 256-bit,
    joined by ','. (Calibration showed a SINGLE mid-frame 64-bit phash cannot tell
    a same-scene clip from a random one — within-scene median 30 vs random 32. A
    multi-frame 256-bit signature with best-frame matching separates cleanly:
    same-scene p90≈106 vs different-scene p10≈112 out of 256.)

Clip-to-clip distance = MIN Hamming over the cross-product of their frame hashes
(best matching frame). Lighting-robust visual-similarity signal:
  - small (≲ NEAR_DUP, ~108/256)  → near-duplicate look (same setup/room)
  - large (≳ 120/256)             → visually distinct

Public API:
  compute_asset_vhash(file_path, kind) -> str | None   # 64-hex or 'h1,h2,..' sig
  hamming(a_sig, b_sig) -> int | None                  # best-frame bit distance
  NEAR_DUP                                              # default near-dup threshold
  ensure_column(con)                                   # idempotent ALTER TABLE
  backfill(limit=None, kinds=("photo","video")) -> dict # CLI entry

CLI:
  .venv/bin/python -m agents.visual_hash --backfill            # all missing
  .venv/bin/python -m agents.visual_hash --backfill --kind video
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("agents.visual_hash")

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

# imagehash.phash hash_size=16 → 256-bit hash, str() = 64 hex chars.
_HASH_SIZE = 16
# Frames sampled across a video clip for its signature (fractions of duration).
_VIDEO_FRAME_POS = (0.2, 0.4, 0.6, 0.8)
# Default near-duplicate threshold (best-frame Hamming, out of 256). Calibrated:
# same-scene p90≈106, different-scene p10≈112. ~108 sits in the gap.
NEAR_DUP = int(os.getenv("VIS_PHASH_NEARDUP", "108"))


def _resolve(file_path: str) -> Path | None:
    """assets.file_path may be relative to ROOT or absolute."""
    if not file_path:
        return None
    p = Path(file_path)
    if not p.is_absolute():
        p = ROOT / p
    return p if p.exists() else None


def _phash_image(path: Path) -> str | None:
    try:
        import imagehash
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            return str(imagehash.phash(im, hash_size=_HASH_SIZE))
    except Exception as e:
        log.warning("image phash failed for %s: %s", path, e)
        return None


def _video_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip()) if out.stdout.strip() else None
    except Exception:
        return None


def _phash_video(path: Path) -> str | None:
    """Signature = several frames sampled across the clip, each a 256-bit phash,
    joined by ','. A single frame of a moving pet is too noisy to identify a scene;
    multiple frames + best-match (in `hamming`) make the signal robust."""
    dur = _video_duration(path)
    if dur and dur > 0:
        times = [round(dur * f, 3) for f in _VIDEO_FRAME_POS]
    else:
        times = [0.5]  # unknown duration → at least one frame
    hashes: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            out = Path(td) / f"f{i}.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t:.3f}",
                     "-i", str(path), "-frames:v", "1", "-q:v", "2", str(out)],
                    capture_output=True, text=True, timeout=60)
            except Exception as e:
                log.warning("video frame extract failed for %s @%ss: %s", path, t, e)
                continue
            if out.exists():
                h = _phash_image(out)
                if h:
                    hashes.append(h)
        if not hashes:
            # retry one frame at t=0 for very short / odd clips
            out = Path(td) / "f0.jpg"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, text=True, timeout=60)
            if out.exists():
                h = _phash_image(out)
                if h:
                    hashes.append(h)
    return ",".join(hashes) if hashes else None


def compute_asset_vhash(file_path: str, kind: str) -> str | None:
    """Perceptual hash/signature for one asset. None if the file is missing
    (e.g. iCloud-only — never force a download here) or extraction fails.
    Photo → one 64-hex hash; video → 'h1,h2,..' multi-frame signature."""
    p = _resolve(file_path)
    if p is None:
        return None
    if kind == "video":
        return _phash_video(p)
    return _phash_image(p)


def hamming(a_sig: str, b_sig: str) -> int | None:
    """Best-frame Hamming distance between two perceptual hashes/signatures.

    Each arg is one hex hash or a ','-joined signature (videos). The distance is
    the MIN over the cross-product of frames (the best-matching frame pair), so a
    clip is "near" another if ANY representative frame looks alike. Returns None if
    nothing comparable (equal-length hex pairs)."""
    if not a_sig or not b_sig:
        return None
    best: int | None = None
    for a in a_sig.split(","):
        ai = a.strip()
        if not ai:
            continue
        for b in b_sig.split(","):
            bi = b.strip()
            if not bi or len(ai) != len(bi):
                continue
            try:
                d = bin(int(ai, 16) ^ int(bi, 16)).count("1")
            except (ValueError, TypeError):
                continue
            if best is None or d < best:
                best = d
    return best


def ensure_column(con: sqlite3.Connection) -> None:
    """Idempotent — add assets.vis_phash if it isn't there yet."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(assets)").fetchall()}
    if "vis_phash" not in cols:
        con.execute("ALTER TABLE assets ADD COLUMN vis_phash TEXT")
        con.commit()
        log.info("added assets.vis_phash column")


def backfill(limit: int | None = None, kinds: tuple[str, ...] = ("photo", "video"),
             progress_every: int = 100) -> dict:
    """Compute vis_phash for every asset that is missing one and is present on disk.
    iCloud-only / missing files are left NULL (retried on a later run once local)."""
    con = sqlite3.connect(str(DB_PATH), timeout=60)
    con.row_factory = sqlite3.Row
    ensure_column(con)
    placeholders = ",".join("?" for _ in kinds)
    rows = con.execute(
        f"SELECT asset_id, file_path, kind FROM assets "
        f"WHERE kind IN ({placeholders}) AND (vis_phash IS NULL OR vis_phash='') "
        f"ORDER BY captured_iso DESC" + (f" LIMIT {int(limit)}" if limit else ""),
        kinds).fetchall()
    total = len(rows)
    done = skipped = failed = 0
    log.info("vis_phash backfill: %d assets to process (kinds=%s)", total, kinds)
    for i, r in enumerate(rows, 1):
        h = compute_asset_vhash(r["file_path"], r["kind"])
        if h is None:
            # distinguish missing-file (skip) from extract failure
            if _resolve(r["file_path"]) is None:
                skipped += 1
            else:
                failed += 1
            continue
        con.execute("UPDATE assets SET vis_phash=? WHERE asset_id=?", (h, r["asset_id"]))
        done += 1
        if i % progress_every == 0:
            con.commit()
            log.info("  ... %d/%d (ok=%d skip=%d fail=%d)", i, total, done, skipped, failed)
    con.commit()
    con.close()
    res = {"total": total, "hashed": done, "skipped_missing": skipped, "failed": failed}
    log.info("vis_phash backfill done: %s", res)
    return res


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Perceptual visual hashing for assets")
    ap.add_argument("--backfill", action="store_true", help="compute missing vis_phash")
    ap.add_argument("--kind", choices=["photo", "video"], help="restrict to one kind")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.backfill:
        kinds = (args.kind,) if args.kind else ("photo", "video")
        res = backfill(limit=args.limit, kinds=kinds)
        print(res)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
