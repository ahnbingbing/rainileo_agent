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
def bulk_download_missing(album_name: str, dry_run: bool = False) -> bool:
    """
    Use osxphotos's built-in CLI to fetch every missing item in the album from
    iCloud. Side effect: Photos.app's local library cache fills up so the next
    PhotoInfo.path lookup returns valid local paths.

    The CLI export writes copies into a temp dir which is auto-cleaned. We
    don't keep those files; we just need the cache population side effect.

    Returns True if invoked, False if osxphotos CLI was not found.
    """
    cli = shutil.which("osxphotos")
    if not cli:
        log.warning("osxphotos CLI not on PATH — cannot bulk fetch missing items")
        return False
    if dry_run:
        log.info("[dry-run] would run: osxphotos export --album %r --download-missing --use-photos-export", album_name)
        return False

    with tempfile.TemporaryDirectory(prefix="rl_dlmiss_") as tmp:
        cmd = [
            cli, "export",
            "--album", album_name,
            "--download-missing",
            "--use-photos-export",
            "--skip-edited",
            "--skip-bursts",
            "--retry", "2",
            tmp,
        ]
        log.info("bulk-fetching missing originals via osxphotos CLI (this is the slow step):")
        log.info("  %s", " ".join(cmd[:-1]) + f" {tmp}")
        log.info("  Photos.app must stay running. Don't sleep the Mac.")
        try:
            proc = subprocess.run(cmd, check=False)
            if proc.returncode != 0:
                log.warning("osxphotos export rc=%d (some items may have failed)", proc.returncode)
        except FileNotFoundError:
            log.warning("osxphotos CLI invocation failed — binary not found at runtime")
            return False
        except KeyboardInterrupt:
            log.warning("bulk download interrupted by user")
            return True
    return True


def insert_asset(con: sqlite3.Connection, **kw) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO assets
            (asset_id, source, kind, file_path, captured_iso, ingested_iso,
             duration_sec, width, height, phash, subjects_csv, age_tag,
             location_tag, notes)
        VALUES (:asset_id, :source, :kind, :file_path, :captured_iso,
                COALESCE(:ingested_iso, datetime('now')),
                :duration_sec, :width, :height, :phash,
                :subjects_csv, :age_tag, :location_tag, :notes)
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
    limit: int | None = None,
    dry_run: bool = False,
    download_missing: bool = False,
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

    # Bulk download from iCloud BEFORE filtering, so that even items with
    # since/limit applied still benefit from the cache population.
    if download_missing:
        missing_count = sum(1 for p in photos if not p.path)
        log.info("missing locally: %d / %d album items", missing_count, len(photos))
        if missing_count > 0:
            invoked = bulk_download_missing(album_name, dry_run=dry_run)
            if invoked and not dry_run:
                log.info("re-opening Photos library to pick up newly-cached paths")
                photosdb = osxphotos.PhotosDB()
                photos = list(photosdb.photos(albums=[album_name]))
                still_missing = sum(1 for p in photos if not p.path)
                log.info("after bulk download: %d items still missing", still_missing)

    if since:
        photos = [p for p in photos if p.date and p.date.date() >= since]
    if limit:
        photos = photos[:limit]

    log.info("album '%s': %d items to consider (after filters)", album_name, len(photos))

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH)
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
        if p.ismissing or not p.path:
            summary["skipped_missing"] += 1
            return

        captured = p.date or dt.datetime.fromtimestamp(Path(p.path).stat().st_mtime)
        captured_iso = captured.isoformat(timespec="seconds")
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

        is_video = bool(getattr(p, "ismovie", False))
        kind = "video" if is_video else "photo"
        target_dir = _year_dir(CLIPS_DIR if is_video else PHOTOS_DIR, captured)
        asset_id = _asset_id(captured, "icloud", p.uuid)

        if asset_exists(con, asset_id):
            summary["skipped_existing"] += 1
            return

        # Direct copy from Photos.app's local file. HEIC stays HEIC —
        # pillow-heif handles it for pHash, ffmpeg handles it for video
        # editing. Items that are still missing after the optional
        # bulk-download phase are silently skipped here; the bulk phase
        # already logged anything that went wrong.
        src_path = Path(p.path) if p.path else None
        if not src_path or not src_path.exists():
            summary["skipped_missing"] += 1
            return

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
    ap.add_argument("--limit", type=int, help="cap items processed (for testing)")
    ap.add_argument("--subject-map", help="comma list 'name=id,...' or JSON dict; merged over defaults")
    ap.add_argument("--dry-run", action="store_true", help="don't copy files or write DB rows")
    ap.add_argument("--download-missing", action="store_true",
                    help="if file isn't local, ask Photos.app to fetch from iCloud (slow, per-item AppleScript)")
    ap.add_argument("--watch", action="store_true", help="poll forever (use launchd in prod)")
    ap.add_argument("--interval", type=int, default=int(os.getenv("ICLOUD_SYNC_INTERVAL", "900")),
                    help="seconds between polls when --watch (default 900 = 15 min)")
    args = ap.parse_args(argv)

    subject_map = parse_subject_map(args.subject_map)
    since = dt.date.fromisoformat(args.since) if args.since else None

    def _one():
        try:
            s = sync_album(
                args.album,
                subject_map=subject_map,
                since=since,
                limit=args.limit,
                dry_run=args.dry_run,
                download_missing=args.download_missing,
            )
            print_summary(s)
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
