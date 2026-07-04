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
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("icloud.sync")

# ── Cross-process osxphotos / PhotoKit serialization ─────────────────────────
# EVERY osxphotos export (--download-missing pulls originals via PhotoKit) and every
# PhotosDB() open MUST be serialized across ALL processes: the 03:00 launchd batch, the
# 06:00 daily sync, and any CLI/Claude session. Two osxphotos touching the Photos library
# at once contend on PhotoKit and BOTH hang to their timeout — the 6/19 batch lost all 4
# slots exactly this way (a parallel session left osxphotos running overnight, so the
# batch's prefetches ran concurrently and timed out at 600s).
#
# The lock path MUST be a FIXED absolute path. It used to be tempfile.gettempdir()/...,
# but $TMPDIR differs per process/session (launchd's own temp vs /tmp/claude-501/...), so
# the two lock files never matched and the "serialization" silently did nothing.
_OSXPHOTOS_LOCK = ROOT / "data" / ".osxphotos.lock"
_osxphotos_lock_tls = threading.local()


@contextlib.contextmanager
def _osxphotos_lock(wait_s: float = 900.0):
    """Serialize ALL osxphotos/PhotoKit access via ONE fixed lock file, across every
    process and session. Re-entrant within a thread (nested calls are no-ops, so wrapping
    both a PhotosDB open and a later export never self-deadlocks). If another process holds
    the lock longer than wait_s, log loudly and proceed WITHOUT exclusivity — a stuck
    holder then surfaces in the logs instead of silently starving the batch forever."""
    depth = getattr(_osxphotos_lock_tls, "depth", 0)
    if depth:                                    # already held by this thread → no-op
        _osxphotos_lock_tls.depth = depth + 1
        try:
            yield
        finally:
            _osxphotos_lock_tls.depth -= 1
        return
    _OSXPHOTOS_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lk = open(_OSXPHOTOS_LOCK, "w")
    acquired = False
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() - start > wait_s:
                    log.warning("osxphotos lock held by another process >%.0fs — proceeding "
                                "WITHOUT exclusivity; a stuck osxphotos may be running "
                                "(check for leftover sessions/renders)", wait_s)
                    break
                time.sleep(2.0)
        _osxphotos_lock_tls.depth = 1
        yield
    finally:
        _osxphotos_lock_tls.depth = 0
        if acquired:
            try:
                fcntl.flock(lk, fcntl.LOCK_UN)
            except Exception:
                pass
        lk.close()

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR", str(ROOT / "data" / "assets" / "photos"))).resolve()
CLIPS_DIR  = Path(os.getenv("CLIPS_DIR",  str(ROOT / "data" / "assets" / "clips"))).resolve()
LOGS_DIR   = Path(os.getenv("LOGS_DIR",   str(ROOT / "data" / "logs"))).resolve()

# Apple content-labels (Korean, this library) that flag a candidate Leo/Ryani photo.
# Used by --pet-labels to find pet photos across the WHOLE library, not just the album
# (PD 2026-06-21 '펫 사진 누락 방지'). Deliberately dog/cat-specific — broad labels like
# 동물/포유동물 over-include; the VLM tagger does the final Leo/Ryani filtering anyway.
PET_LABELS = ["개", "고양이", "불도그", "토이 불독", "걸어가는 개", "사냥개",
              "새끼고양이", "범무늬 고양이", "고양이아과", "갯과의 동물"]

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
    # phash exists for RF footage dedup / visual-similarity freshness, whose consumers
    # operate on recent candidate footage — not on bulk archival-library backfill. For a
    # HEIC, imagehash.phash only needs a 64×64 thumbnail, but PIL must software-decode the
    # FULL grid image first (libheif decode + rotate + colorspace = a multi-core CPU hog,
    # ~0.25-2s/photo). At hundreds of photos/round that decode, not the network, is the
    # bottleneck. ICLOUD_SKIP_PHASH=1 (set by the chunked backlog) skips it; phash=None is
    # already a valid stored state and can be backfilled later by a dedicated pass.
    if os.getenv("ICLOUD_SKIP_PHASH") == "1":
        return None
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


def _osxphotos_available() -> bool:
    """True ONLY where osxphotos can actually run: macOS + the CLI present.

    On the cloud VM (Linux) this is False. The render path (agents/cameraman.py)
    checks this before any Photos-library pull so that off-Mac it stays GCS-only —
    the GCS mirror is ~100% complete, and any rare miss is handled by the per-cut
    swap/drop gate instead of attempting an osxphotos export that can't work there.
    """
    import sys as _sys
    if _sys.platform != "darwin":
        return False
    return _osxphotos_cli() is not None


def _osxphotos_healthy(probe_timeout: float = 45.0) -> bool:
    """Quick probe: is the Photos library opening at normal speed right now?

    The 6/21 batch wipeout was a TRANSIENT slow window — Photos took hours to open,
    so every `osxphotos export` blew its timeout. A fast `query --count` (which opens
    the same library) completing within `probe_timeout` means we're in a healthy window
    and a download will succeed; a timeout means we're inside a slow window and should
    back off and retry later rather than burn a long export budget that's doomed.
    Serialized under the shared lock so the probe doesn't itself contend.
    """
    cli = _osxphotos_cli()
    if not cli:
        return False
    try:
        with _osxphotos_lock(wait_s=probe_timeout + 30):
            subprocess.run([cli, "query", "--count"], check=True,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=probe_timeout)
        return True
    except Exception:
        return False


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
        "--download-missing",
        "--update",               # reuse prior export DB → no interactive prompt
        method,
        "--skip-edited",
        "--skip-bursts",
        "--filename", "{uuid}",   # name by UUID so we can map exported→PhotoInfo
        "--retry", "2",
    ]
    # PD 2026-06-13: scope to an explicit NEW-uuid list (preferred — no date to
    # remember). Written to a file so a large list never blows the arg limit. The uuid
    # list IS the scope — do NOT also pass --album (PD 2026-06-21: that intersected the
    # two and dropped every pet-label photo that lives OUTSIDE the album → 0 exported).
    uuid_file: Path | None = None
    if uuids:
        uuid_file = dest_dir / ".new_uuids.txt"
        uuid_file.write_text("\n".join(uuids) + "\n")
        cmd += ["--uuid-from-file", str(uuid_file)]
    else:
        # Whole-album mode: scope by album, and (PD 2026-06-12) by date so an unscoped
        # --download-missing doesn't re-pull every pruned original in the album.
        cmd[3:3] = ["--album", album_name]
        if since:
            cmd += ["--added-after", since.isoformat()]
    log.info("Option A export (download-missing → %s, filename={uuid}):", dest_dir)
    log.info("  %s", " ".join(cmd))
    try:
        with _osxphotos_lock():   # don't run concurrently with a render-time prefetch
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
    # SERIALIZE osxphotos exports with the shared cross-process lock (see _osxphotos_lock):
    # the launch batch, the daily sync, and any other session must never hit the Photos
    # library at once or both osxphotos calls hang. The lock is held only for the subprocess.
    try:
        with _osxphotos_lock():
            subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, timeout=180)
    except Exception as e:
        log.warning("download_asset_by_uuid failed for %s: %s", uuid, e)
        return None
    matches = sorted(Path(dest_dir).glob(f"{uuid}.*"))
    plain = [m for m in matches if " (" not in m.name]
    cand = (plain or matches)
    return str(cand[0]) if cand else None


def download_assets_by_uuids(uuids: "list[str]", dest_dir: Path,
                             timeout: float = 300.0,
                             max_attempts: int = 6) -> "dict[str, str]":
    """Bulk on-demand fetch: download MANY originals by Photos UUID in a SINGLE
    osxphotos export (one library scan for the whole set, not one per photo).

    PD 2026-06-16: the prefetch used to call download_asset_by_uuid once PER
    photo, and EACH call makes osxphotos re-scan the entire Photos library
    (~20s on the 400k-item DB) before downloading. Seven photos = seven scans;
    under launch contention every scan + download blew the 90s/photo budget and
    the AV slot got 0/7 → 0 cuts → skipped. One --uuid-from-file export scans
    once and exports all of them in seconds. Returns {uuid: local_path} for the
    ones that arrived (missing keys = caller drops/swaps that cut).

    PD 2026-06-17: the bulk fix above DROPPED the per-photo retry (3× backoff)
    that the old download_asset_by_uuid loop had — so a TRANSIENT PhotoKit
    throttle (library scans fine but the iCloud download silently returns 0
    under self-heal-loop contention) now yields 0/N with no retry, and the AV
    slot empties in the 03:00 batch. RESTORE the retry at the bulk layer: after
    each export, collect what landed and RE-EXPORT ONLY the still-missing uuids
    (smaller set scans faster) after a short backoff, up to max_attempts, all
    within the overall `timeout` budget. The same photos download fine in
    isolation, so a retry on a transient 0 is the direct cure.
    """
    cli = _osxphotos_cli()
    uuids = [u for u in (uuids or []) if u]
    if not cli or not uuids:
        return {}
    dest_dir.mkdir(parents=True, exist_ok=True)
    method = os.getenv("ICLOUD_EXPORT_METHOD", "--use-photokit")

    def _export(targets: "list[str]", subprocess_timeout: float) -> None:
        uuid_file = dest_dir / ".prefetch_uuids.txt"
        uuid_file.write_text("\n".join(targets) + "\n")
        cmd = [cli, "export", str(dest_dir), "--uuid-from-file", str(uuid_file),
               "--download-missing", "--update", method, "--skip-edited",
               "--skip-bursts", "--filename", "{uuid}", "--retry", "2"]
        # Shared cross-process lock — one osxphotos export at a time across the launch
        # batch, the daily sync, and any other session (see _osxphotos_lock).
        with _osxphotos_lock():
            subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL,
                           timeout=subprocess_timeout)

    def _landed(u: str) -> str | None:
        matches = sorted(Path(dest_dir).glob(f"{u}.*"))
        cand = [m for m in matches
                if not m.name.endswith(".txt") and " (" not in m.name]
        cand = cand or [m for m in matches if not m.name.endswith(".txt")]
        return str(cand[0]) if cand else None

    out: "dict[str, str]" = {}
    pending = list(uuids)
    start = time.monotonic()
    # Cap EACH attempt so a hung export (slow osxphotos window) fails fast and leaves
    # budget for the health-probe + backoff + retry below — otherwise the first attempt
    # eats the whole budget on one doomed hang and the slow-window retry never runs.
    per_attempt = float(os.getenv("PREFETCH_ATTEMPT_S", "200"))
    for attempt in range(max(1, max_attempts)):
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            break
        try:
            _export(pending, min(remaining, per_attempt))
        except Exception as e:
            log.warning("download_assets_by_uuids export attempt %d failed "
                        "(%d uuids): %s", attempt + 1, len(pending), e)
            # fall through — pick up whatever did land before the timeout
        for u in list(pending):
            fp = _landed(u)
            if fp:
                out[u] = fp
        pending = [u for u in pending if u not in out]
        if not pending:
            break
        if attempt + 1 < max(1, max_attempts):
            log.warning("prefetch: %d/%d still missing after attempt %d — "
                        "retrying just the missing ones", len(pending),
                        len(uuids), attempt + 1)
            # Slow-window aware backoff: if the export came up empty AND a quick health
            # probe shows Photos is opening slowly, we're inside a transient slow window
            # — wait it out with a long backoff (a few minutes) instead of immediately
            # burning another doomed export. Only worth it while budget remains; a truly
            # sustained window is covered by the warm local cache, not by waiting here.
            slow_backoff = float(os.getenv("ICLOUD_SLOW_BACKOFF_S", "90"))
            if not _osxphotos_healthy(probe_timeout=float(os.getenv("ICLOUD_HEALTH_PROBE_S", "45"))):
                backoff = slow_backoff
                log.warning("prefetch: Photos in a SLOW window — backing off %.0fs "
                            "before retry (budget left %.0fs)", backoff,
                            timeout - (time.monotonic() - start))
            else:
                backoff = 1.5 * (attempt + 1)  # healthy: quick retry (1.5s, 3.0s…)
            if timeout - (time.monotonic() - start) > backoff:
                time.sleep(backoff)
    return out


def backfill_uuids(album_name: str) -> int:
    """One-time: fill source_uuid for already-ingested assets (legacy rows had
    none) so they become re-downloadable for the efficient model + cooldown."""
    import osxphotos  # type: ignore
    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)
    log.info("backfill: opening Photos library…")
    with _osxphotos_lock():
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
    """Disk-pressure-aware prune: free the bulky local ORIGINAL only when disk is
    actually tight, and only down to a free-space floor — never the whole library.

    PD 2026-06-21 (download-failure root cause): the old "efficient model" pruned
    EVERY re-downloadable VLM-tagged original after each sync (5554 pruned, 12 left
    on disk). That made every render depend on a healthy osxphotos re-download — so
    a single transient iCloud/Photos slow window (the kind that makes a library open
    take hours and every `osxphotos export` hit its 600s timeout) turned into TOTAL
    render failure across a whole launch batch. With disk headroom now (tens of GB
    free), keeping the working set local is strictly safer and removes the fragile
    re-download from the hot path entirely.

    Policy:
      • If free space ≥ ICLOUD_PRUNE_FREE_FLOOR_GB (default 30) → skip (no-op).
      • Otherwise prune oldest-touched (mtime) first, but NEVER files touched within
        the last ICLOUD_PRUNE_KEEP_DAYS (default 7) — those are the working set a
        recent/upcoming render just downloaded. Stop as soon as free ≥ floor.
    Keeps the DB row + file_path so a render can still re-download on demand if a
    pruned clip is ever needed during a healthy window. Returns (count, bytes_freed).
    """
    floor_gb = float(os.getenv("ICLOUD_PRUNE_FREE_FLOOR_GB", "30"))
    keep_days = float(os.getenv("ICLOUD_PRUNE_KEEP_DAYS", "7"))
    floor_bytes = floor_gb * 1e9

    def _free() -> int:
        try:
            return shutil.disk_usage(str(ROOT)).free
        except Exception:
            return 0

    free0 = _free()
    if free0 >= floor_bytes:
        log.info("prune: skip — %.1f GB free ≥ %.0f GB floor (keeping working set)",
                 free0 / 1e9, floor_gb)
        return 0, 0

    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)
    rows = con.execute(
        "SELECT asset_id, file_path FROM assets WHERE source_uuid IS NOT NULL "
        "AND source_uuid != '' AND vlm_analyzed_at IS NOT NULL"
    ).fetchall()
    con.close()

    now = time.time()
    keep_cutoff = now - keep_days * 86400
    cand = []
    for aid, fp in rows:
        try:
            if fp and os.path.exists(fp):
                mt = os.path.getmtime(fp)
                if mt >= keep_cutoff:
                    continue  # protect the recent working set
                cand.append((mt, aid, fp))
        except Exception as e:
            log.warning("prune stat skip %s: %s", aid, e)
    cand.sort()  # oldest mtime first

    n = 0
    freed = 0
    for mt, aid, fp in cand:
        if _free() + freed >= floor_bytes:
            break  # back above the floor — stop
        try:
            sz = os.path.getsize(fp)
            if not dry_run:
                os.remove(fp)
            freed += sz
            n += 1
        except Exception as e:
            log.warning("prune skip %s: %s", aid, e)
    log.info("%sprune: %d files, %.1f GB (free %.1f→%.1f GB, floor %.0f)",
             "[dry] " if dry_run else "", n, freed / 1e9,
             free0 / 1e9, _free() / 1e9, floor_gb)
    return n, freed


def _pending_card_asset_ids(con: sqlite3.Connection) -> "list[str]":
    """Asset ids referenced by not-yet-published cards for today onward — the clips
    an upcoming launch batch / re-render is most likely to need. Best-effort parse of
    the payload (recommended_assets + per-cut asset_id); card_assets is unused today."""
    ids: list[str] = []
    try:
        rows = con.execute(
            "SELECT payload_json FROM cards WHERE state IN "
            "('approved','rendered','pd_review','draft') AND date >= date('now','-1 day')"
        ).fetchall()
    except Exception:
        return ids
    for (pj,) in rows:
        try:
            d = json.loads(pj)
        except Exception:
            continue
        for a in (d.get("recommended_assets") or []):
            if isinstance(a, str):
                ids.append(a)
            elif isinstance(a, dict) and a.get("asset_id"):
                ids.append(a["asset_id"])
        for c in (d.get("cuts") or []):
            if isinstance(c, dict) and c.get("asset_id"):
                ids.append(c["asset_id"])
    return ids


def warm_working_set(budget_gb: "float | None" = None,
                     progress=None) -> "tuple[int, float]":
    """Pre-download a BOUNDED local working set so launch renders rarely need a
    risky on-demand re-download (which fails during a transient Photos-slow window).

    The prune fix keeps clips that are already local, but after the historical prune
    the cache was cold (≈all originals offloaded), so every memory-lane render still
    had to re-fetch. This proactively warms — in a healthy window, ahead of the 03:00
    batch — the assets most likely to be used, capped at ICLOUD_CACHE_BUDGET_GB so it
    never fills the disk:
      priority 1: assets referenced by pending/upcoming cards (definitely needed),
      priority 2: most-recently-CAPTURED clips+photos (what fresh concepts draw from).
    Stops once the on-disk re-downloadable footprint reaches the budget. Returns
    (downloaded_count, gb_on_disk_after).
    """
    budget_gb = budget_gb if budget_gb is not None else float(
        os.getenv("ICLOUD_CACHE_BUDGET_GB", "25"))
    budget = budget_gb * 1e9
    cli = _osxphotos_cli()
    if not cli:
        log.info("warm: osxphotos unavailable — skip")
        return 0, 0.0
    if not _osxphotos_healthy():
        log.warning("warm: Photos in a slow window — skip warm (try later)")
        return 0, 0.0

    con = sqlite3.connect(DB_PATH)
    ensure_source_uuid_column(con)
    rows = con.execute(
        "SELECT asset_id, source_uuid, file_path, captured_iso FROM assets "
        "WHERE source_uuid IS NOT NULL AND source_uuid != ''"
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    pend = _pending_card_asset_ids(con)
    con.close()

    # priority order: pending-card assets first, then most-recent captures
    ordered, seen = [], set()
    for aid in pend:
        r = by_id.get(aid)
        if r and aid not in seen:
            ordered.append(r); seen.add(aid)
    for r in sorted(rows, key=lambda r: (r[3] or ""), reverse=True):
        if r[0] not in seen:
            ordered.append(r); seen.add(r[0])

    def _bytes_on_disk() -> int:
        tot = 0
        for r in rows:
            fp = r[2]
            if fp and os.path.exists(fp):
                try:
                    tot += os.path.getsize(fp)
                except OSError:
                    pass
        return tot

    on_disk = _bytes_on_disk()
    if on_disk >= budget:
        log.info("warm: cache already %.1f GB ≥ %.0f GB budget — nothing to do",
                 on_disk / 1e9, budget_gb)
        return 0, on_disk / 1e9

    # collect uuids for assets NOT yet local, stop adding once we'd exceed budget
    missing = []
    projected = on_disk
    for aid, uuid, fp, _ in ordered:
        if fp and os.path.exists(fp):
            continue
        missing.append((aid, uuid, fp))
        projected += 30 * 1e6  # rough avg clip/photo size estimate for the cap
        if projected >= budget:
            break
    if not missing:
        log.info("warm: working set already cached (%.1f GB)", on_disk / 1e9)
        return 0, on_disk / 1e9

    # Never warm so aggressively that free disk drops below the prune floor (else the
    # next prune would just evict what we warmed). Keep a 5 GB margin above the floor.
    floor_gb = float(os.getenv("ICLOUD_PRUNE_FREE_FLOOR_GB", "30"))
    free_stop = (floor_gb + 5) * 1e9

    def _free() -> int:
        try:
            return shutil.disk_usage(str(ROOT)).free
        except Exception:
            return 0

    log.info("warm: fetching ~%d assets toward %.0f GB budget (on disk %.1f GB, "
             "free %.1f GB, stop if free<%.0f GB)…",
             len(missing), budget_gb, on_disk / 1e9, _free() / 1e9, floor_gb + 5)
    got = 0
    # download in chunks; re-check real disk usage between chunks (estimate is rough)
    CHUNK = 25
    staging = (PHOTOS_DIR.parent / "warm_staging")
    for i in range(0, len(missing), CHUNK):
        if _bytes_on_disk() >= budget:
            break
        if _free() <= free_stop:
            log.info("warm: stopping — free disk near prune floor (%.1f GB)",
                     _free() / 1e9)
            break
        chunk = missing[i:i + CHUNK]
        res = download_assets_by_uuids([u for _, u, _ in chunk], staging,
                                       timeout=420.0, max_attempts=2)
        # move/keep: the render path resolves by file_path; copy into the asset's
        # canonical path so future renders find it without re-export.
        for aid, uuid, fp in chunk:
            src = res.get(uuid)
            if src and fp:
                try:
                    Path(fp).parent.mkdir(parents=True, exist_ok=True)
                    if not os.path.exists(fp):
                        # MOVE (not copy) — leaving the staging dup was a real leak:
                        # warm_staging grew to 6.6 GB of never-cleaned exports (PD 2026-06-21).
                        shutil.move(src, fp)
                    elif os.path.exists(src):
                        os.remove(src)   # already cached → drop the redundant staging copy
                    got += 1
                except Exception as e:
                    log.warning("warm: place %s failed: %s", aid, e)
        if progress:
            progress(f"warm {got}/{len(missing)} (disk {_bytes_on_disk()/1e9:.1f} GB)")
    # warm_staging holds only transient {uuid}.ext exports — clear it so it can't
    # accumulate into a multi-GB orphan again.
    try:
        if staging.exists():
            shutil.rmtree(staging)
    except Exception as e:
        log.warning("warm: staging cleanup failed: %s", e)
    final = _bytes_on_disk() / 1e9
    log.info("warm: downloaded %d assets — cache now %.1f GB (budget %.0f)",
             got, final, budget_gb)
    return got, final


def content_hash(path) -> "str | None":
    """md5 of the file BYTES — a content fingerprint that is identical no matter which
    source (iCloud sync vs Slack) or how many times the SAME media was ingested. This is
    the dedup key that source-id keys (Slack file id / photo uuid) cannot provide: the
    same clip uploaded to Slack 3× gets 3 file ids → 3 asset rows, and the same media
    arriving from BOTH iCloud and Slack gets 2 ids — but one content_hash. (PD flagged the
    iCloud↔Slack overlap; source-id dedup let it through.)"""
    import hashlib
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _ensure_content_hash_col(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE assets ADD COLUMN content_hash TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_assets_content_hash ON assets(content_hash)")
    except sqlite3.OperationalError:
        pass  # already added


def find_content_dup(con: sqlite3.Connection, chash: "str | None"):
    """Return an existing asset row (asset_id, file_path) with this content_hash, else None.
    Used to skip creating a duplicate asset for byte-identical media from any source."""
    if not chash:
        return None
    try:
        return con.execute(
            "SELECT asset_id, file_path FROM assets WHERE content_hash = ? LIMIT 1", (chash,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def insert_asset(con: sqlite3.Connection, **kw) -> "str | None":
    """Insert an asset, deduped by CONTENT hash. If a byte-identical asset already exists
    (regardless of source/asset_id), skip the insert and return that existing asset_id so
    the same media never becomes two pool entries. Returns the asset_id actually in the DB."""
    kw.setdefault("source_uuid", None)
    _ensure_content_hash_col(con)
    chash = kw.get("content_hash")
    if chash is None:
        chash = content_hash(kw.get("file_path"))
        kw["content_hash"] = chash
    dup = find_content_dup(con, chash)
    if dup and dup[0] != kw.get("asset_id"):
        log.info("content-dup: %s == existing %s (skip new asset)", kw.get("asset_id"), dup[0])
        return dup[0]
    con.execute(
        """
        INSERT OR IGNORE INTO assets
            (asset_id, source, kind, file_path, captured_iso, ingested_iso,
             duration_sec, width, height, phash, subjects_csv, age_tag,
             location_tag, notes, source_uuid, content_hash)
        VALUES (:asset_id, :source, :kind, :file_path, :captured_iso,
                COALESCE(:ingested_iso, datetime('now')),
                :duration_sec, :width, :height, :phash,
                :subjects_csv, :age_tag, :location_tag, :notes, :source_uuid, :content_hash)
        """,
        kw,
    )
    return kw.get("asset_id")


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
    labels: "list[str] | None" = None,
) -> dict:
    """If `labels` is given, select photos across the WHOLE library whose Apple
    content-labels intersect that set (e.g. 개/고양이/불도그), instead of by album —
    this catches pet photos the human never added to the album (PD 2026-06-21,
    '펫 사진 누락 방지'). Pair with backfill=True to ingest every not-yet-ingested
    match; the VLM tagger then keeps only the ones that are actually Leo/Ryani.
    Default (labels=None) = the original album behaviour, unchanged."""
    try:
        import osxphotos  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "osxphotos not installed. Run:\n"
            "  uv pip install --python ./.venv/bin/python osxphotos pillow-heif"
        ) from e

    log.info("opening Photos library via osxphotos (this can take a few seconds)")
    with _osxphotos_lock():
        photosdb = osxphotos.PhotosDB()
        if labels:
            lset = set(labels)
            photos = [p for p in photosdb.photos()
                      if lset & set(getattr(p, "labels", []) or [])]
            log.info("label-select %s → %d candidate photos (whole library)",
                     labels, len(photos))
        else:
            photos = list(photosdb.photos(albums=[album_name]))
    if not photos:
        if labels:
            raise SystemExit(f"no photos match labels {labels}.")
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

        # PD 2026-06-24 self-heal: an ALREADY-ingested asset whose local file is a
        # 0-BYTE placeholder (a transient osxphotos slow-window left it empty on the
        # 6/13 import) is excluded from NEW ("it's in DB") and never re-pulled, so it
        # sits in permanent limbo — VLM can't extract a frame → can't tag it. Re-pull
        # exactly those and copy them back onto their paths. ⚠️ A MISSING file is NOT
        # broken — this repo prunes local originals that are safely mirrored in GCS
        # (re-fetched on demand at render). Flag ONLY exists-but-0-byte, else we'd
        # treat every pruned original as broken and re-download the archive (disk bomb).
        broken: dict[str, str] = {}
        for _u, _fp in con.execute(
                "SELECT source_uuid, file_path FROM assets "
                "WHERE source_uuid IS NOT NULL AND source_uuid != ''"):
            try:
                _p = Path(_fp)
                if not _p.is_absolute():
                    _p = ROOT / _p
                if _p.exists() and _p.stat().st_size == 0:
                    broken[_u] = str(_p)
            except Exception:
                continue
        if broken and not dry_run:
            log.info("self-heal: %d ingested assets have 0-byte files — re-downloading",
                     len(broken))
            repair_dir = ROOT / "data" / "tmp" / "icloud_repair"
            shutil.rmtree(repair_dir, ignore_errors=True)
            bulk_export_to(album_name, repair_dir, uuids=sorted(broken))
            _repaired = 0
            for _u, _dst in broken.items():
                _cands = [c for c in sorted(repair_dir.glob(f"{_u}.*"))
                          if not c.name.endswith(".txt") and c.stat().st_size > 0]
                if not _cands:
                    continue
                _d = Path(_dst)
                _d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_cands[0], _d)
                _repaired += 1
            log.info("self-heal: repaired %d/%d 0-byte files", _repaired, len(broken))
            shutil.rmtree(repair_dir, ignore_errors=True)

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
    ap.add_argument("--warm", action="store_true",
                    help="pre-download a bounded local working-set cache (pending-card "
                         "assets + recent captures, up to ICLOUD_CACHE_BUDGET_GB) so "
                         "launch renders rarely need a risky on-demand re-download.")
    ap.add_argument("--pet-labels", action="store_true",
                    help="ingest by Apple content-label across the WHOLE library "
                         "(개/고양이/불도그/…), not just the album — catches pet photos "
                         "never added to the album. VLM keeps only Leo/Ryani. Pair with "
                         "--download-missing --vlm; --backfill ingests all matches.")
    ap.add_argument("--labels", help="comma list of content-labels to select by "
                    "(overrides the --pet-labels default set)")
    args = ap.parse_args(argv)

    if args.backfill_uuids:
        n = backfill_uuids(args.album)
        print(f"backfill: set source_uuid on {n} rows")
        if not (args.prune or args.warm):
            return 0
    # Standalone warm only when no sync intent (otherwise it runs AFTER the sync).
    if args.warm and not (args.download_missing or args.vlm or args.watch):
        got, gb = warm_working_set()
        print(f"warm: downloaded {got} assets — cache now {gb:.1f} GB")
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
    # Pet-photo coverage (PD 2026-06-21): select by Apple content-label across the
    # whole library so album-omitted pet photos still get in. VLM filters to Leo/Ryani.
    sel_labels = None
    if args.labels:
        sel_labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    elif args.pet_labels:
        sel_labels = PET_LABELS

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
                labels=sel_labels,
            )
            print_summary(s)
            # PD 2026-06-07: tag newly-imported assets so the Writer can use
            # them. tag_assets_vlm (default = untagged only) picks up exactly
            # the new ones.
            imported = (s.get("imported_photos", 0) + s.get("imported_clips", 0)
                        + s.get("imported_live_clips", 0))
            if args.vlm and not args.dry_run:
                # PD 2026-06-24: tag EVERY untagged asset, not just this run's imports.
                # A prior run's transient Gemini block/empty-response, or a 0-byte file
                # recovered by the self-heal re-download below, leaves assets untagged —
                # and a routine sync imports nothing new, so they'd sit in limbo forever
                # ("no new assets → skipping VLM" was exactly that bug). tag_assets_vlm
                # selects untagged only, so this is a fast no-op when the backlog is clean.
                try:
                    _c = sqlite3.connect(DB_PATH)
                    _untagged = _c.execute(
                        "SELECT COUNT(*) FROM assets WHERE vlm_analyzed_at IS NULL "
                        "OR vlm_analyzed_at=''").fetchone()[0]
                    _c.close()
                except Exception:
                    _untagged = 0
                if imported > 0 or _untagged > 0:
                    _run_vlm_tagging(max(imported, _untagged))
                else:
                    log.info("--vlm: nothing untagged — skipping VLM tagging")
            # Keep the GCS mirror current (PD 2026-06-21) — BEFORE prune, so an asset is
            # safely in GCS before its local original can be deleted. Uploads newly-imported
            # captures + anything warm/backfill pulled local. Idempotent; non-fatal.
            if not args.dry_run:
                try:
                    from icloud import gcs as _gcs
                    if _gcs.enabled():
                        n = _gcs.mirror_local()
                        log.info("post-sync GCS mirror: %d assets present in GCS", n)
                except Exception:
                    log.exception("post-sync GCS mirror failed (non-fatal)")
            # Efficient model: after mirroring, free the bulky originals (kept
            # re-downloadable by uuid AND now in GCS). Daily steady-state stays small;
            # the chunked backlog driver sets KEEP_DAYS=0 to delete each batch right away.
            if args.prune and not args.dry_run:
                n, freed = prune_originals()
                log.info("post-sync prune: %d files, %.1f GB freed", n, freed / 1e9)
            # Warm a bounded working-set cache so the next launch batch rarely needs a
            # risky on-demand re-download. Runs in this (healthy, post-sync) window.
            if args.warm and not args.dry_run:
                got, gb = warm_working_set()
                log.info("post-sync warm: +%d assets, cache %.1f GB", got, gb)
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
