"""GCS asset mirror — fast, reliable fetch path that replaces the fragile
osxphotos/iCloud/Photos-library download for the RENDER path (PD 2026-06-21).

Root cause it fixes: render needed old memory-lane photos that prune had offloaded,
so every batch re-downloaded them via osxphotos → open the 35GB Photos library →
PhotoKit pull from iCloud, during the 3-6am Photos maintenance window, gated by local
disk pressure. Every layer there is a failure point; the download itself is ~1s.

Mirror once to gs://<bucket>/<relpath-under-data/assets> (keyed by the same on-disk
layout, so the blob name is derivable from a row's file_path). Then the pipeline pulls
a missing original from GCS first — no library open, no PhotoKit, no dawn window, no
osxphotos lock. osxphotos stays ONLY for ingesting brand-new captures (which then get
uploaded here too).

Degrades safely: if disabled, the lib is missing, the blob isn't there, or anything
errors, every entry returns None/False so callers fall back to the osxphotos path.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("icloud.gcs")
ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = ROOT / "data" / "assets"

BUCKET = os.getenv("GCS_ASSET_BUCKET", "rianileo-assets")


def enabled() -> bool:
    return os.getenv("GCS_ASSETS", "1") == "1"


_client_tls = threading.local()


def _client():
    """One storage.Client per thread (the SDK client isn't thread-safe to share)."""
    c = getattr(_client_tls, "c", None)
    if c is None:
        from google.cloud import storage  # lazy: only when GCS is actually used
        c = storage.Client(project=os.getenv("GCP_PROJECT") or None)
        _client_tls.c = c
    return c


def _bucket():
    return _client().bucket(BUCKET)


def blob_name(file_path: str) -> str | None:
    """Derive the GCS object name from a local asset path: its path relative to
    data/assets/ (e.g. /…/data/assets/photos/2016/med_x.jpg → photos/2016/med_x.jpg).
    Returns None for paths outside the assets tree."""
    if not file_path:
        return None
    try:
        rel = Path(file_path).resolve().relative_to(ASSETS_ROOT.resolve())
    except (ValueError, OSError):
        # Not under data/assets (or unresolved) — fall back to basename grouping by kind.
        return None
    return str(rel)


def list_blob_names(prefix: str = "") -> set[str]:
    """All object names under `prefix` in ONE paginated listing — far cheaper than a
    per-asset exists() HEAD when checking thousands (used by the backfill work-list)."""
    if not enabled():
        return set()
    try:
        return {b.name for b in _client().list_blobs(BUCKET, prefix=prefix)}
    except Exception as e:
        log.warning("gcs list_blob_names failed: %s", e)
        return set()


def exists(file_path: str) -> bool:
    if not enabled():
        return False
    name = blob_name(file_path)
    if not name:
        return False
    try:
        return _bucket().blob(name).exists()
    except Exception as e:
        log.warning("gcs exists() failed for %s: %s", name, e)
        return False


def download_to(file_path: str) -> str | None:
    """Pull the blob for this asset path down to `file_path`. Returns the local path
    on success, None if the blob isn't in GCS or anything fails (caller falls back)."""
    if not enabled():
        return None
    name = blob_name(file_path)
    if not name:
        return None
    try:
        blob = _bucket().blob(name)
        if not blob.exists():
            return None
        dest = Path(file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".gcsdl")
        blob.download_to_filename(str(tmp))
        os.replace(tmp, dest)  # atomic; no half-written file if interrupted
        log.info("gcs: fetched %s (%.1f MB)", name, dest.stat().st_size / 1e6)
        return str(dest)
    except Exception as e:
        log.warning("gcs download_to failed for %s: %s", name, e)
        return None


def upload(file_path: str) -> bool:
    """Mirror one local asset to GCS (skip if already present + same size).
    Returns True if the blob is in GCS after the call (uploaded or already there)."""
    if not enabled():
        return False
    name = blob_name(file_path)
    if not name or not Path(file_path).exists():
        return False
    try:
        blob = _bucket().blob(name)
        if blob.exists():
            blob.reload()
            if blob.size == Path(file_path).stat().st_size:
                return True  # already mirrored
        blob.upload_from_filename(file_path)
        return True
    except Exception as e:
        log.warning("gcs upload failed for %s: %s", name, e)
        return False


OUTPUT_ROOT = ROOT / "data" / "output"


def upload_episode(file_path: str) -> str | None:
    """Mirror a finished episode/output mp4 to gs://<bucket>/output/<relpath-under-data/output>
    (PD 2026-07-04). After the GCP cutover the render runs on the VM, so its local disk is the
    only copy of a produced video (YouTube is the PUBLISH target, not a browse UI, and Slack
    sometimes drops the file). Mirroring every output to ONE GCS prefix gives PD (and any node)
    a reliable place to review all produced episodes: `gs://<bucket>/output/episodes/`. Returns
    the gs:// URI on success, None otherwise. Best-effort — never raises."""
    if not enabled():
        return None
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        rel = p.resolve().relative_to(OUTPUT_ROOT.resolve())
        name = f"output/{rel}"
    except (ValueError, OSError):
        name = f"output/episodes/{p.name}"
    try:
        blob = _bucket().blob(name)
        if blob.exists():
            blob.reload()
            if blob.size == p.stat().st_size:
                return f"gs://{BUCKET}/{name}"
        blob.upload_from_filename(str(p))
        log.info("gcs: mirrored output %s (%.1f MB)", name, p.stat().st_size / 1e6)
        return f"gs://{BUCKET}/{name}"
    except Exception as e:
        log.warning("gcs upload_episode failed for %s: %s", p.name, e)
        return None


def mirror_local(limit: int | None = None, max_workers: int = 16) -> int:
    """Upload every local asset not yet mirrored (idempotent). Keeps the GCS mirror
    complete as new captures import and warm pulls offloaded originals back local.
    Returns the count of assets present in GCS after the run."""
    if not enabled():
        return 0
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor, as_completed
    con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    rows = con.execute(
        "SELECT file_path FROM assets WHERE file_path IS NOT NULL AND file_path != ''"
    ).fetchall()
    con.close()
    paths = []
    for (fp,) in rows:
        p = fp if Path(fp).is_absolute() else str(ROOT / fp)
        if Path(p).exists():
            paths.append(p)
    if limit:
        paths = paths[:limit]
    ok = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed([ex.submit(upload, p) for p in paths]):
            try:
                if fut.result():
                    ok += 1
            except Exception:
                pass
    return ok
