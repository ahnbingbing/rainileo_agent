"""
icloud/sync.py — Sync the macOS Photos.app "Ryani & Leo" album into the project.

Runs locally on macOS, reads the Photos.app library directly via osxphotos
(github.com/RhetTbull/osxphotos). No iCloud login, no API key, no network.

Typical use:
    # First bulk import — pulls everything in the album, year-organizes,
    # writes asset rows. Idempotent: safe to re-run.
    python -m icloud.sync --album "Ryani & Leo"

    # Test on a small batch first
    python -m icloud.sync --album "Ryani & Leo" --limit 20 --dry-run

    # Daemon loop (used by launchd plist)
    python -m icloud.sync --album "Ryani & Leo" --watch --interval 900

Output layout:
    data/assets/photos/<YYYY>/<original_filename>.jpeg
    data/assets/clips/<YYYY>/<original_filename>.mov     # iPhone videos + Live Photos

Subject auto-tagging:
    Pulls Photos.app's People & Pets tags. Maps them to our subject IDs via
    --subject-map (defaults: 랴니/Ryani -> ryani, 레오/Leo -> leo).

Age-tag inference:
    ryani only + capture < 2020-05-05 -> 'youth'  (first 5 yrs)
    ryani only + capture >= 2020-05-05 -> 'adult'
    leo only   + capture < 2026-09-25 -> 'kitten' (first 1 yr)
    leo only   + capture >= 2026-09-25 -> 'adult'
    both subjects -> 'mixed'
    unknown -> NULL (manual or downstream classifier)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("icloud.sync")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR", str(ROOT / "data" / "assets" / "photos"))).resolve()
CLIPS_DIR  = Path(os.getenv("CLIPS_DIR",  str(ROOT / "data" / "assets" / "clips"))).resolve()
LOGS_DIR   = Path(os.getenv("LOGS_DIR",   str(ROOT / "data" / "logs"))).resolve()

DEFAULT_SUBJECT_MAP = {
    "랴니": "ryani", "Ryani": "ryani", "ryani": "ryani", "riani": "ryani",
    "레오": "leo",   "Leo": "leo",     "leo": "leo",
}

# Photos.app auto-label hints. The album is curated as "Ryani & Leo" so we
# treat any cat-related label as Leo and any dog-related label as Ryani.
# Photos.app uses Korean labels on Korean macOS, English on English macOS;
# we accept both. Exact set match (not substring) avoids false positives like
# "개구리" (frog) accidentally matching "개" (dog).
CAT_LABELS = {
    # Korean
    "고양이", "고양이과", "고양이아과", "고양잇과", "새끼고양이",
    "도메스틱 쇼트헤어", "유러피언 쇼트헤어", "아메리칸 쇼트헤어",
    "브리티시 쇼트헤어", "페르시안", "샴", "랙돌", "메인쿤", "벵갈",
    # English
    "cat", "kitten", "kitty", "feline",
}
DOG_LABELS = {
    # Korean — French Bulldog (Ryani's breed) gets multiple synonyms
    "개", "강아지", "도그", "반려견",
    "불도그", "프렌치 불도그", "잉글리시 불도그",
    "시바견", "시바", "리트리버", "푸들", "닥스훈트", "비글",
    # English
    "dog", "puppy", "doggy", "bulldog", "french bulldog",
}

# Birth/transition dates used for age_tag inference.
RYANI_BORN  = dt.date(2015, 5, 5)
RYANI_ADULT = dt.date(2020, 5, 5)   # 5 yrs old
LEO_BORN    = dt.date(2025, 9, 25)
LEO_ADULT   = dt.date(2026, 9, 25)  # 1 yr old


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def _asset_id(captured: dt.datetime, source: str, src_id: str) -> str:
    return f"med_{captured.strftime('%Y_%m_%d_%H%M%S')}_{source}_{_short_hash(src_id)}"


def _year_dir(base: Path, captured: dt.datetime | None) -> Path:
    year = str(captured.year) if captured else "unsorted"
    d = base / year
    d.mkdir(parents=True, exist_ok=True)
    return d


def infer_age_tag(subjects: list[str], captured: dt.date | None) -> str | None:
    if not captured or not subjects:
        return None
    has_r = "ryani" in subjects
    has_l = "leo" in subjects
    if has_r and has_l:
        return "mixed"
    if has_r:
        return "youth" if captured < RYANI_ADULT else "adult"
    if has_l:
        return "kitten" if captured < LEO_ADULT else "adult"
    return None


def map_subjects(
    person_names: Iterable[str],
    subject_map: dict[str, str],
    labels: Iterable[str] | None = None,
) -> list[str]:
    """
    Resolve subject IDs ('ryani', 'leo') for a photo.

    Two signals, unioned:
      1. Photos.app person/pet *names* — for users who tag faces.
      2. Photos.app auto *labels* (categories like '고양이', 'dog') — works
         without manual tagging. Album curation ("Ryani & Leo") guarantees
         any cat is Leo and any dog is Ryani.
    """
    out: set[str] = set()

    # Signal 1: named persons/pets
    for n in person_names or []:
        if not n or n == "_UNKNOWN_":
            continue
        sid = subject_map.get(n)
        if sid:
            out.add(sid)

    # Signal 2: Photos.app auto labels (case-insensitive for English)
    if labels:
        label_set_orig = set(labels)
        label_set_lower = {l.lower() for l in labels}
        if label_set_orig & CAT_LABELS or label_set_lower & {l.lower() for l in CAT_LABELS}:
            out.add("leo")
        if label_set_orig & DOG_LABELS or label_set_lower & {l.lower() for l in DOG_LABELS}:
            out.add("ryani")

    return sorted(out)


def compute_phash(path: Path) -> str | None:
    try:
        from PIL import Image
        import imagehash  # type: ignore
        # pillow-heif registers HEIF/HEIC opener if installed
        try:
            from pillow_heif import register_heif_opener  # type: ignore
            register_heif_opener()
        except ImportError:
            pass
        img = Image.open(path).convert("RGB")
        return str(imagehash.phash(img, hash_size=16))
    except Exception as e:
        log.debug("phash skipped (%s): %s", path.name, e)
        return None


# ──────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────
def asset_exists(con: sqlite3.Connection, asset_id: str) -> bool:
    row = con.execute("SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
    return row is not None


# ──────────────────────────────────────────────────────────────────────
# Bulk download via osxphotos CLI (handles version differences for us)
# ──────────────────────────────────────────────────────────────────────
def _run_vlm_tagging(n_new: int) -> None:
    """PD 2026-06-07: after ingest, VLM-tag the newly-imported (untagged)
    assets. Runs scripts/tag_assets_vlm.py (default selects vlm_analyzed_at IS
    NULL). Non-fatal — a tagging failure must not fail the sync."""
    import sys as _sys
    script = Path(__file__).resolve().parent.parent / "scripts" / "tag_assets_vlm.py"
    if not script.exists():
        log.warning("tag_assets_vlm.py not found — skipping VLM tagging")
        return
    log.info("running VLM tagging on ~%d newly-imported assets…", n_new)
    try:
        rc = subprocess.run([_sys.executable, str(script)], check=False).returncode
        log.info("VLM tagging finished (rc=%d)", rc)
    except Exception as e:
        log.warning("VLM tagging failed: %s", e)


def _osxphotos_cli() -> str | None:
    cli = shutil.which("osxphotos")
    if cli:
        return cli
    # PD 2026-06-07: under launchd / background the venv bin isn't on PATH.
    import sys as _sys
    cand = Path(_sys.executable).parent / "osxphotos"
    if cand.exists():
        log.info("osxphotos not on PATH — using venv CLI: %s", cand)
        return str(cand)
    log.warning("osxphotos CLI not on PATH and not in venv (%s)", cand)
    return None


def bulk_export_to(album_name: str, dest_dir: Path, dry_run: bool = False,
                   since: "dt.date | None" = None,
                   uuids: "list[str] | None" = None) -> bool:
    """PD 2026-06-07 (Option A): export the album to `dest_dir` with each file
    named by the photo UUID ({uuid}.<ext>), downloading iCloud-only originals
    via PhotoKit. We then INGEST FROM THESE EXPORTED FILES (our own copies) —
    not from Photos' volatile local path (which Optimize-Mac-Storage keeps
    evicting). Once copied into data/assets, an asset never disappears again.

    PD 2026-06-13: when `uuids` is given, export EXACTLY those items (via
    --uuid-from-file) instead of the whole album — the caller diffs the album
    against already-ingested source_uuids and passes only the NEW ones, so a
    routine sync never re-pulls the archive and no manual date scope is needed.
    Returns True if the export ran."""
    cli = _osxphotos_cli()
    if not cli:
        return False
    method = os.getenv("ICLOUD_EXPORT_METHOD", "--use-photokit")
    if dry_run:
        log.info("[dry-run] would run: osxphotos export %s --album %r %s "
                 "--download-missing --filename {uuid} (uuids=%s)",
                 dest_dir, album_name, method,
                 "all" if not uuids else f"{len(uuids)} new")
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        cli, "export", str(dest_dir),
        "--album", album_name,
        "--download-missing",
        "--update",               # reuse prior export DB → no interactive prompt
        method,
        "--skip-edited",
        "--skip-bursts",
        "--filename", "{uuid}",   # name by UUID so we can map exported→PhotoInfo
        "--retry", "2",
    ]
    # PD 2026-06-13: scope to an explicit NEW-uuid list (preferred — no date to
    # remember). Written to a file so a large list never blows the arg limit.
    uuid_file: Path | None = None
    if uuids:
        uuid_file = dest_dir / ".new_uuids.txt"
        uuid_file.write_text("\n".join(uuids) + "\n")
        cmd += ["--uuid-from-file", str(uuid_file)]
    # PD 2026-06-12: WITHOUT scoping, --download-missing re-downloads EVERY pruned
    # original in the album (7306 items under the efficient-storage model). Scope the
    # export to items ADDED to the library on/after `since` so a routine sync only
    # pulls NEW content, not the whole archive.
    elif since:
        cmd += ["--added-after", since.isoformat()]
    log.info("Option A export (download-missing → %s, filename={uuid}):", dest_dir)
    log.info("  %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            log.warning("osxphotos export rc=%d (some items may have failed)", proc.returncode)
    except FileNotFoundError:
        log.warning("osxphotos CLI invocation failed — binary not found at runtime")
        return False
    except KeyboardInterrupt:
        log.warning("bulk export interrupted by user")
        return True
    return True


def _resolve_local_source(p, export_dir: Path | None):
    """Find a REAL local file for photo `p`. Prefer Photos' own local path; if
    evicted (Optimize Storage), use the UUID-named file we just exported."""
    try:
        if p.path and Path(p.path).exists():
            return Path(p.path)
    except Exception:
        pass
    if export_dir:
        try:
            matches = sorted(Path(export_dir).glob(f"{p.uuid}.*"))
            # skip osxphotos increment dupes like "{uuid} (1).ext"; prefer plain
            plain = [m for m in matches if " (" not in m.name]
            for m in (plain or matches):
                if m.exists():
                    return m
        except Exception:
            pass
    return None


def ensure_source_uuid_column(con: sqlite3.Connection) -> None:
    """PD 2026-06-07 (efficient model): store the Photos UUID so the original
    can be RE-DOWNLOADED on demand at render time — letting us delete bulky
    originals after VLM tagging without losing the asset."""
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(assets)")]
        if "source_uuid" not in cols:
            con.execute("ALTER TABLE assets ADD COLUMN source_uuid TEXT")
            con.commit()
    except Exception as e:
        log.warning("ensure source_uuid column failed: %s", e)


def download_asset_by_uuid(uuid: str, dest_dir: Path) -> str | None:
    """Efficient-model on-demand fetch: download ONE original by Photos UUID
    (for a clip selected at render time). Returns the local file path or None."""
    cli = _osxphotos_cli()
    if not cli or not uuid:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    method = os.getenv("ICLOUD_EXPORT_METHOD", "--use-photokit")
    # PD 2026-06-08: --update reuses the prior export DB so osxphotos does NOT
    # prompt "found previous export database ... continue? [y/N]" (that interactive
    # prompt hung the render at photo_i2v re-download). stdin=DEVNULL + timeout are
    # belt-and-suspenders so it can never block a render/cron.
    cmd = [cli, "export", str(dest_dir), "--uuid", uuid, "--download-missing",
           "--update", method, "--skip-edited", "--skip-bursts",
           "--filename", "{uuid}", "--retry", "2"]
    # PD 2026-06-10: SERIALIZE osxphotos exports with a cross-process file lock.
    # The launch batch runs an RF cut and an AV cut concurrently; two osxphotos
    # exports hitting the Photos library at once made one transiently fail (return
    # nothing) — which killed the whole AV slot (av went 0/2 on 6/11). One export
    # at a time removes that contention. The lock is held only for the subprocess.
    import fcntl as _fcntl
    import tempfile as _tempfile
    _lockpath = Path(_tempfile.gettempdir()) / "rianileo_osxphotos.lock"
    try:
        with open(_lockpath, "w") as _lk:
            _fcntl.flock(_lk, _fcntl.LOCK_EX)
            try:
                subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, timeout=180)
            finally:
                _fcntl.flock(_lk, _fcntl.LOCK_UN)
    except Exception as e:
        log.warning("download_asset_by_uuid failed for %s: %s", uuid, e)
        return None
    matches = sorted(Path(dest_dir).glob(f"{uuid}.*"))
    plain = [m for m in matches if " (" not in m.name]
    cand = (plain or matches)
    return str(cand[0]) if cand else None


def backfill_uuids(album_name: str) -> int:
    """One-time: fill source_uuid for already-ingested assets (legacy rows had
    none) so they become re-downloadable for the efficient model + cooldown."""
    import osxphotos  # type: ignore
    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)
    log.info("backfill: opening Photos library…")
    photosdb = osxphotos.PhotosDB()
    photos = list(photosdb.photos(albums=[album_name]))
    n = 0
    for p in photos:
        captured = p.date or dt.datetime.now()
        for src in ("icloud", "icloud_live"):
            aid = _asset_id(captured, src, p.uuid)
            cur = con.execute(
                "UPDATE assets SET source_uuid=? WHERE asset_id=? "
                "AND (source_uuid IS NULL OR source_uuid='')", (p.uuid, aid))
            n += cur.rowcount
    con.commit()
    con.close()
    log.info("backfill: set source_uuid on %d rows", n)
    return n


def prune_originals(dry_run: bool = False) -> tuple[int, int]:
    """Efficient model: delete the local ORIGINAL file for assets that are
    VLM-tagged AND have a source_uuid (re-downloadable). Keeps the DB row +
    file_path; render re-downloads on demand. Returns (count, bytes_freed)."""
    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)
    rows = con.execute(
        "SELECT asset_id, file_path FROM assets WHERE source_uuid IS NOT NULL "
        "AND source_uuid != '' AND vlm_analyzed_at IS NOT NULL"
    ).fetchall()
    con.close()
    n = 0
    freed = 0
    for aid, fp in rows:
        try:
            if fp and os.path.exists(fp):
                sz = os.path.getsize(fp)
                if not dry_run:
                    os.remove(fp)
                freed += sz
                n += 1
        except Exception as e:
            log.warning("prune skip %s: %s", aid, e)
    log.info("%sprune: %d files, %.1f GB", "[dry] " if dry_run else "", n, freed / 1e9)
    return n, freed


def insert_asset(con: sqlite3.Connection, **kw) -> None:
    kw.setdefault("source_uuid", None)
    con.execute(
        """
        INSERT OR IGNORE INTO assets
            (asset_id, source, kind, file_path, captured_iso, ingested_iso,
             duration_sec, width, height, phash, subjects_csv, age_tag,
             location_tag, notes, source_uuid)
        VALUES (:asset_id, :source, :kind, :file_path, :captured_iso,
                COALESCE(:ingested_iso, datetime('now')),
                :duration_sec, :width, :height, :phash,
                :subjects_csv, :age_tag, :location_tag, :notes, :source_uuid)
        """,
        kw,
    )


# ──────────────────────────────────────────────────────────────────────
# Core sync
# ──────────────────────────────────────────────────────────────────────
def sync_album(
    album_name: str,
    *,
    subject_map: dict[str, str],
    since: dt.date | None = None,
    added_since: dt.date | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    download_missing: bool = False,
    backfill: bool = False,
) -> dict:
    try:
        import osxphotos  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "osxphotos not installed. Run:\n"
            "  uv pip install --python ./.venv/bin/python osxphotos pillow-heif"
        ) from e

    log.info("opening Photos library via osxphotos (this can take a few seconds)")
    photosdb = osxphotos.PhotosDB()

    photos = list(photosdb.photos(albums=[album_name]))
    if not photos:
        raise SystemExit(
            f"album '{album_name}' has no photos (or doesn't exist).\n"
            f"  available albums: {', '.join(a.title for a in photosdb.album_info)[:200]} ..."
        )

    # PD 2026-06-07 (Option A): export the album (downloading iCloud-only
    # originals) to a UUID-named export dir, then INGEST FROM THOSE FILES — not
    # from Photos' volatile local path. This makes downloads stick: once copied
    # into data/assets they never disappear (Optimize Mac Storage can't evict
    # our own copies). `export_dir` is consumed by _process_one below.
    # DB connection opened early so the download step can diff the album against
    # already-ingested source_uuids (PD 2026-06-13: download NEW items only).
    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)

    export_dir: Path | None = None
    if download_missing:
        # PD 2026-06-13: don't make the human remember a date scope. Auto-detect
        # what's genuinely NEW since the last sync and download only that, so a
        # routine sync stays small — an unscoped full-album pull (~7306 items →
        # 37G) once filled the disk to 100% (ENOSPC). See memory
        # `icloud_export_disk_bomb`.
        #
        # "New" is NOT merely "not in DB" — the album holds years of history
        # (~8000 items were never ingested), and treating all of those as new
        # would re-create the disk bomb every run. Instead derive a self-advancing
        # watermark = the newest date_added among items WE'VE ALREADY INGESTED;
        # anything added AFTER that and not yet ingested is a genuine new addition.
        ingested = {
            row[0] for row in con.execute(
                "SELECT source_uuid FROM assets "
                "WHERE source_uuid IS NOT NULL AND source_uuid != ''")
        }

        def _date_added(p):
            da = getattr(p, "date_added", None)
            return da.date() if da else None

        ingested_dates = [d for p in photos if p.uuid in ingested
                          and (d := _date_added(p)) is not None]
        # explicit scope (--since/--added-since) overrides the auto watermark;
        # else use newest-ingested date_added; else (empty DB) None = bootstrap.
        watermark = (added_since or since
                     or (max(ingested_dates) if ingested_dates else None))
        # PD 2026-06-13 (#2): the watermark MISSES below-watermark items that were in the
        # album but never ingested (a same-location photo PD wanted wasn't in the DB).
        # --backfill (ICLOUD_BACKFILL=1) ingests EVERY in-album item not in the DB
        # (uuid diff, ignore watermark) — bounded by the threshold guard so a big backfill
        # is an explicit opt-in.
        _backfill = backfill or os.getenv("ICLOUD_BACKFILL") == "1"
        if _backfill or watermark is None:
            new_photos = [p for p in photos if p.uuid not in ingested]
            if _backfill:
                log.info("BACKFILL: ingesting all %d in-album items not in DB",
                         len(new_photos))
        else:
            new_photos = [p for p in photos
                          if p.uuid not in ingested
                          and (_date_added(p) or dt.date.min) >= watermark]
        # PD 2026-06-14: CHUNKED full backfill. The whole ~8000-item old backlog must be
        # ingested+VLM-tagged (it's why the Writer pool skews recent), but downloading it
        # all at once is the disk bomb. ICLOUD_BACKFILL_BATCH_BYTES caps THIS run's download
        # to ~N bytes worth of un-ingested items (oldest date_added first → rebalance old
        # years). A driver loop (scripts/icloud_full_sync_chunked.sh) re-runs until empty:
        # each run downloads ≤N bytes → ingest → VLM → prune originals → next batch.
        _batch_bytes = int(os.getenv("ICLOUD_BACKFILL_BATCH_BYTES", "0"))
        if _backfill and _batch_bytes > 0 and new_photos:
            new_photos.sort(key=lambda p: (_date_added(p) or dt.date.min))
            picked, acc = [], 0
            for p in new_photos:
                sz = int(getattr(p, "original_filesize", 0) or 0)
                if picked and acc + sz > _batch_bytes:
                    break
                picked.append(p)
                acc += sz
            log.info("BACKFILL batch: picked %d items (~%.2f GB) of %d un-ingested "
                     "(oldest-first)", len(picked), acc / 1e9, len(new_photos))
            new_photos = picked
        log.info("album=%d, ingested=%d, watermark(date_added)>=%s, NEW to download=%d",
                 len(photos), len(ingested), watermark, len(new_photos))
        if not new_photos:
            log.info("nothing new in the album — skipping download")
        else:
            # Safety net: a very large NEW set means either the first-ever
            # bootstrap or a wiped/empty DB. Don't silently pull the archive and
            # fill the disk — require an explicit opt-in (or a date scope).
            full_threshold = int(os.getenv("ICLOUD_NEW_FULL_THRESHOLD", "400"))
            if (len(new_photos) > full_threshold
                    and (added_since or since) is None
                    and os.getenv("ICLOUD_ALLOW_FULL_EXPORT") != "1"):
                raise SystemExit(
                    f"{len(new_photos)} new items (likely first bootstrap / empty "
                    f"DB) — refusing to bulk-download (disk-fill risk). Scope with "
                    f"--added-since YYYY-MM-DD, or set ICLOUD_ALLOW_FULL_EXPORT=1 "
                    f"to download them all."
                )
            export_dir = ROOT / "data" / "tmp" / "icloud_export"
            shutil.rmtree(export_dir, ignore_errors=True)
            if not dry_run:
                bulk_export_to(album_name, export_dir, dry_run=dry_run,
                               since=(added_since or since),
                               uuids=[p.uuid for p in new_photos])

    # PD 2026-06-12: when the user ADDS OLD videos (years-old capture dates), filter by
    # date_added — NOT capture date — so they actually register. (--since filters by
    # capture date and dropped every old clip the user just imported.)
    if added_since:
        def _added(p):
            da = getattr(p, "date_added", None)
            return da.date() if da else None
        photos = [p for p in photos if (_added(p) or dt.date.min) >= added_since]
        log.info("filter date_added >= %s → %d items", added_since, len(photos))
    elif since:
        photos = [p for p in photos if p.date and p.date.date() >= since]
    if limit:
        photos = photos[:limit]

    log.info("album '%s': %d items to consider (after filters)", album_name, len(photos))

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "considered": len(photos),
        "skipped_existing": 0,
        "skipped_missing": 0,
        "skipped_hidden": 0,
        "imported_photos": 0,
        "imported_clips": 0,
        "imported_live_clips": 0,
        "errors": 0,
        "by_year": {},
        "by_subjects": {"ryani": 0, "leo": 0, "both": 0, "unknown": 0},
    }

    try:
        from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
        progress_ctx: object = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
        )
    except ImportError:
        progress_ctx = None

    def _process_one(p) -> None:
        # Validity filters
        if p.hidden:
            summary["skipped_hidden"] += 1
            return

        captured = p.date or dt.datetime.now()
        captured_iso = captured.isoformat(timespec="seconds")

        is_video = bool(getattr(p, "ismovie", False))
        kind = "video" if is_video else "photo"
        target_dir = _year_dir(CLIPS_DIR if is_video else PHOTOS_DIR, captured)
        asset_id = _asset_id(captured, "icloud", p.uuid)

        # Skip already-ingested BEFORE resolving/downloading the source.
        if asset_exists(con, asset_id):
            summary["skipped_existing"] += 1
            return

        # PD 2026-06-07 (Option A): get a REAL local file — Photos' own path if
        # present, else the UUID-named file we exported (downloaded from iCloud).
        src_path = _resolve_local_source(p, export_dir)
        if not src_path or not src_path.exists():
            summary["skipped_missing"] += 1
            return

        year_key = captured.strftime("%Y")
        summary["by_year"][year_key] = summary["by_year"].get(year_key, 0) + 1

        # Subject + age tagging
        person_names = [pi.name for pi in (p.person_info or []) if pi.name]
        labels = list(getattr(p, "labels", None) or [])
        subjects = map_subjects(person_names, subject_map, labels=labels)
        subjects_csv = ",".join(subjects) if subjects else None
        age_tag = infer_age_tag(subjects, captured.date())

        if subjects == ["leo", "ryani"]:
            summary["by_subjects"]["both"] += 1
        elif subjects == ["ryani"]:
            summary["by_subjects"]["ryani"] += 1
        elif subjects == ["leo"]:
            summary["by_subjects"]["leo"] += 1
        else:
            summary["by_subjects"]["unknown"] += 1

        # Copy the resolved local file (Photos path or our exported copy) into
        # data/assets — HEIC stays HEIC (pillow-heif/ffmpeg handle it).
        ext = src_path.suffix.lower() or (".mov" if is_video else ".jpg")
        dest_path = target_dir / f"{asset_id}{ext}"

        if not dry_run and not dest_path.exists():
            try:
                shutil.copy2(src_path, dest_path)
            except Exception as e:
                log.warning("copy failed for %s: %s", p.uuid, e)
                summary["errors"] += 1
                return

        if is_video:
            summary["imported_clips"] += 1
        else:
            summary["imported_photos"] += 1

        phash = None if is_video else (None if dry_run else compute_phash(dest_path))

        if not dry_run:
            insert_asset(
                con,
                asset_id=asset_id,
                source="icloud",
                source_uuid=p.uuid,
                kind=kind,
                file_path=str(dest_path),
                captured_iso=captured_iso,
                ingested_iso=None,
                duration_sec=getattr(p, "duration", None),
                width=p.width,
                height=p.height,
                phash=phash,
                subjects_csv=subjects_csv,
                age_tag=age_tag,
                location_tag=None,
                notes=f"uuid:{p.uuid}; title:{p.title or ''}",
            )

        # Live Photo: paired .mov is its own asset
        if getattr(p, "live_photo", False) and getattr(p, "path_live_photo", None):
            try:
                live_target = _year_dir(CLIPS_DIR, captured)
                live_src = Path(p.path_live_photo)
                live_dest = live_target / live_src.name
                live_id = _asset_id(captured, "icloud_live", p.uuid)
                if asset_exists(con, live_id):
                    return
                if not dry_run and not live_dest.exists():
                    shutil.copy2(live_src, live_dest)
                if not dry_run:
                    insert_asset(
                        con,
                        asset_id=live_id,
                        source="icloud",
                        source_uuid=p.uuid,
                        kind="video",
                        file_path=str(live_dest),
                        captured_iso=captured_iso,
                        ingested_iso=None,
                        duration_sec=None,
                        width=p.width,
                        height=p.height,
                        phash=None,
                        subjects_csv=subjects_csv,
                        age_tag=age_tag,
                        location_tag=None,
                        notes=f"uuid:{p.uuid}; live_photo_pair_of:{asset_id}",
                    )
                summary["imported_live_clips"] += 1
            except Exception as e:
                log.debug("live photo extract skipped for %s: %s", p.uuid, e)

    # Run loop with optional progress bar
    if progress_ctx is not None:
        with progress_ctx as bar:
            task = bar.add_task("syncing", total=len(photos))
            for p in photos:
                try:
                    _process_one(p)
                except Exception:
                    log.exception("error processing %s", getattr(p, "uuid", "?"))
                    summary["errors"] += 1
                bar.advance(task)
    else:
        for i, p in enumerate(photos, 1):
            try:
                _process_one(p)
            except Exception:
                log.exception("error processing %s", getattr(p, "uuid", "?"))
                summary["errors"] += 1
            if i % 50 == 0:
                log.info("  %d / %d", i, len(photos))

    if not dry_run:
        con.commit()
    con.close()
    # Option A: the exported copies are now safe in data/assets — drop the temp
    # export dir (can be many GB).
    if export_dir and not dry_run:
        shutil.rmtree(export_dir, ignore_errors=True)
    return summary


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def print_summary(s: dict) -> None:
    print("\n=== sync summary ===")
    print(f"  considered:       {s['considered']}")
    print(f"  skipped existing: {s['skipped_existing']}")
    print(f"  skipped missing:  {s['skipped_missing']}")
    print(f"  skipped hidden:   {s['skipped_hidden']}")
    print(f"  imported photos:  {s['imported_photos']}")
    print(f"  imported clips:   {s['imported_clips']}")
    print(f"  live photo clips: {s['imported_live_clips']}")
    print(f"  errors:           {s['errors']}")
    by_year = sorted(s["by_year"].items())
    print("  by year:")
    for y, n in by_year:
        print(f"    {y}: {n}")
    print("  by subject:")
    for k, n in s["by_subjects"].items():
        print(f"    {k:>7}: {n}")
    print()


def parse_subject_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return DEFAULT_SUBJECT_MAP.copy()
    out = DEFAULT_SUBJECT_MAP.copy()
    if raw.startswith("{"):
        out.update(json.loads(raw))
    else:
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    ap = argparse.ArgumentParser(prog="icloud.sync")
    ap.add_argument("--album", required=False, default=os.getenv("ICLOUD_ALBUM", "Ryani & Leo"),
                    help="Photos.app album name to sync (default: 'Ryani & Leo')")
    ap.add_argument("--since", help="YYYY-MM-DD; only process items captured on/after this date")
    ap.add_argument("--added-since", help="YYYY-MM-DD; only process items ADDED to the "
                    "library on/after this date (use when importing OLD footage — filters "
                    "by date_added, not capture date, and scopes the export to --added-after)")
    ap.add_argument("--limit", type=int, help="cap items processed (for testing)")
    ap.add_argument("--subject-map", help="comma list 'name=id,...' or JSON dict; merged over defaults")
    ap.add_argument("--dry-run", action="store_true", help="don't copy files or write DB rows")
    ap.add_argument("--backfill", action="store_true",
                    help="ingest EVERY in-album item not yet in the DB (uuid diff, "
                         "ignores the date watermark) — for un-ingested below-watermark "
                         "photos. Bounded by ICLOUD_NEW_FULL_THRESHOLD / "
                         "ICLOUD_ALLOW_FULL_EXPORT=1 for large backfills.")
    ap.add_argument("--download-missing", action="store_true",
                    help="if file isn't local, ask Photos.app to fetch from iCloud (slow, per-item AppleScript)")
    ap.add_argument("--watch", action="store_true", help="poll forever (use launchd in prod)")
    ap.add_argument("--interval", type=int, default=int(os.getenv("ICLOUD_SYNC_INTERVAL", "900")),
                    help="seconds between polls when --watch (default 900 = 15 min)")
    ap.add_argument("--vlm", action="store_true",
                    help="after ingest, run VLM tagging on the newly-imported "
                         "(untagged) assets so the Writer can use them (PD 2026-06-07)")
    ap.add_argument("--backfill-uuids", action="store_true",
                    help="one-time: fill source_uuid for already-ingested assets")
    ap.add_argument("--prune", action="store_true",
                    help="efficient model: delete local originals for VLM-tagged "
                         "+ uuid'd assets (re-downloadable on demand). Frees space.")
    args = ap.parse_args(argv)

    if args.backfill_uuids:
        n = backfill_uuids(args.album)
        print(f"backfill: set source_uuid on {n} rows")
        if not args.prune:
            return 0
    # Standalone prune only when no sync intent. Otherwise prune runs AFTER the
    # sync (download→ingest→VLM→prune) — see _one() below.
    if args.prune and not (args.download_missing or args.vlm or args.watch):
        n, freed = prune_originals(dry_run=args.dry_run)
        print(f"prune: removed {n} originals, freed {freed/1e9:.1f} GB")
        return 0

    subject_map = parse_subject_map(args.subject_map)
    since = dt.date.fromisoformat(args.since) if args.since else None
    added_since = dt.date.fromisoformat(args.added_since) if args.added_since else None

    def _one():
        try:
            s = sync_album(
                args.album,
                subject_map=subject_map,
                since=since,
                added_since=added_since,
                limit=args.limit,
                dry_run=args.dry_run,
                download_missing=args.download_missing,
                backfill=args.backfill,
            )
            print_summary(s)
            # PD 2026-06-07: tag newly-imported assets so the Writer can use
            # them. tag_assets_vlm (default = untagged only) picks up exactly
            # the new ones.
            imported = (s.get("imported_photos", 0) + s.get("imported_clips", 0)
                        + s.get("imported_live_clips", 0))
            if args.vlm and not args.dry_run and imported > 0:
                _run_vlm_tagging(imported)
            elif args.vlm and imported == 0:
                log.info("--vlm: no new assets imported — skipping VLM tagging")
            # Efficient model: after tagging, free the bulky originals (kept
            # re-downloadable by uuid). Daily steady-state stays small.
            if args.prune and not args.dry_run:
                n, freed = prune_originals()
                log.info("post-sync prune: %d files, %.1f GB freed", n, freed / 1e9)
            return 0
        except SystemExit:
            raise
        except Exception:
            log.exception("sync failed")
            return 1

    if args.watch:
        log.info("watching album '%s' every %d s (Ctrl-C to stop)", args.album, args.interval)
        while True:
            _one()
            time.sleep(args.interval)
    return _one()


if __name__ == "__main__":
    sys.exit(main())
