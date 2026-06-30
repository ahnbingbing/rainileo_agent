"""
scripts/slack_sync.py — unified Slack channel → DB sync.

Built 2026-05-31 in response to the discovery that `background_refs` (41 rows
from 2026-05-25) had no current ingest path. Every Slack channel that feeds
the production system must have a routing entry here.

Channels watched (all configured via env vars):
- SLACK_BACKGROUND_CHANNEL → `background_refs` table (image + PD-written
  description for one space — drop-in for Veo/Seedance prompts)
- SLACK_EPISODE_CHANNEL → `episode_stories` table (free-form story seeds)
- SLACK_PHOTOS_CHANNEL → `assets` table (raw photos/videos — queued for VLM
  tagging via existing `scripts/tag_assets_vlm.py` pipeline)

Idempotency:
- Each row stores `slack_ts` (channel-unique). Re-running the script skips
  already-ingested timestamps.
- `slack_sync_state` table tracks `last_ts` per channel so we only fetch new
  messages on each run.

Run:
    python3 scripts/slack_sync.py                  # sync all channels since last
    python3 scripts/slack_sync.py --channel background
    python3 scripts/slack_sync.py --since 2026-05-25  # override last_ts
    python3 scripts/slack_sync.py --dry-run
    python3 scripts/slack_sync.py --bootstrap      # ingest from beginning (no last_ts)

Run on cron (or /loop) to keep DB in sync:
    */15 * * * * cd <repo> && .venv/bin/python scripts/slack_sync.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("slack_sync")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

BG_DIR = ROOT / "assets" / "backgrounds"
PHOTOS_DIR = ROOT / "data" / "assets" / "photos"
CLIPS_DIR = ROOT / "data" / "assets" / "clips"

# Channel routing: env var name → handler name. Handlers below.
CHANNEL_ROUTES = {
    "background": {
        "env": "SLACK_BACKGROUND_CHANNEL",
        "handler": "ingest_background",
    },
    "episode": {
        "env": "SLACK_EPISODE_CHANNEL",
        "handler": "ingest_episode",
    },
    "photos": {
        "env": "SLACK_PHOTOS_CHANNEL",
        "handler": "ingest_media",
    },
    # Grandparents (할머니·할아버지 at 충주) share Ryani/Leo footage here — same raw
    # media → assets ingest as #photos, so their clips/photos enter the Writer pool.
    "grandmompapa": {
        "env": "SLACK_GRANDMOMPAPA_CHANNEL",
        "handler": "ingest_media",
    },
}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # State table for last_ts tracking
    con.execute("""
        CREATE TABLE IF NOT EXISTS slack_sync_state (
            channel_id   TEXT PRIMARY KEY,
            channel_name TEXT,
            last_ts      TEXT NOT NULL DEFAULT '0',
            last_synced  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    return con


def _client():
    from slack_sdk import WebClient
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not set")
    return WebClient(token=token)


def get_last_ts(con: sqlite3.Connection, channel_id: str) -> str:
    row = con.execute(
        "SELECT last_ts FROM slack_sync_state WHERE channel_id=?", (channel_id,)
    ).fetchone()
    return row["last_ts"] if row else "0"


def set_last_ts(con: sqlite3.Connection, channel_id: str, channel_name: str, ts: str) -> None:
    con.execute(
        "INSERT INTO slack_sync_state (channel_id, channel_name, last_ts) VALUES (?, ?, ?) "
        "ON CONFLICT(channel_id) DO UPDATE SET last_ts=excluded.last_ts, "
        "channel_name=excluded.channel_name, last_synced=datetime('now')",
        (channel_id, channel_name, ts),
    )
    con.commit()


def fetch_messages(client, channel_id: str, oldest: str) -> list[dict]:
    """Fetch messages newer than `oldest` (Slack ts string). Handles pagination."""
    all_msgs: list[dict] = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id, oldest=oldest, limit=200, cursor=cursor,
        )
        all_msgs.extend(resp.get("messages", []))
        meta = resp.get("response_metadata", {}) or {}
        cursor = meta.get("next_cursor") or None
        if not cursor:
            break
        # Slack tier-3 rate limit
        time.sleep(1.0)
    # Slack returns newest-first; we want oldest-first for sequential ingest
    return sorted(all_msgs, key=lambda m: float(m.get("ts", "0")))


def download_file(client, file_obj: dict, target_dir: Path,
                  prefix: str = "") -> Path | None:
    """Download a Slack-hosted file (image/video) to local dir. Returns path."""
    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    # Filename: prefix + slack file id + original ext
    file_id = file_obj.get("id", "x")
    name = file_obj.get("name", f"{file_id}.bin")
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if prefix:
        out = target_dir / f"{prefix}_{file_id}{ext}"
    else:
        out = target_dir / f"{file_id}{ext}"
    if out.exists() and out.stat().st_size > 0:
        return out
    import urllib.request
    token = os.environ.get("SLACK_BOT_TOKEN")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out.write_bytes(resp.read())
        return out
    except Exception as e:
        log.warning("download failed for %s: %s", file_id, e)
        return None


def _ffprobe_bin() -> str:
    import shutil
    return shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def extract_captured_at(path: Path) -> "datetime | None":
    """Real capture timestamp from a media file's own metadata — so a Slack-shared
    clip/photo (which has no Photos.app date) still clusters by its TRUE shoot date
    (outings / memory-lane), not its Slack upload day. Video → ffprobe
    format_tags=creation_time; image → EXIF DateTimeOriginal (HEIC via pillow_heif).
    Returns a tz-aware UTC datetime, or None when the file carries no capture date."""
    ext = path.suffix.lower()
    try:
        if ext in (".mov", ".mp4", ".m4v", ".avi", ".webm", ".mkv"):
            out = subprocess.run(
                [_ffprobe_bin(), "-v", "error", "-show_entries",
                 "format_tags=creation_time", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30).stdout.strip()
            if out:
                s = out.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return None
        # image
        from PIL import Image
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        exif = Image.open(path).getexif()
        raw = None
        try:
            raw = exif.get_ifd(0x8769).get(36867)  # ExifIFD → DateTimeOriginal
        except Exception:
            pass
        raw = raw or exif.get(306)                  # DateTime (fallback)
        if raw:
            return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
    except Exception as e:
        log.debug("capture-date extract failed (%s): %s", path.name, e)
    return None


# ────────────────────────────────────────────────────────────────────────────
# Channel handlers
# ────────────────────────────────────────────────────────────────────────────
def ingest_background(con: sqlite3.Connection, client, msg: dict,
                       dry_run: bool = False) -> str:
    """#background — image + text PD description → background_refs."""
    ts = msg.get("ts", "")
    text = (msg.get("text") or "").strip()
    files = msg.get("files") or []
    images = [f for f in files if (f.get("mimetype") or "").startswith("image/")]
    if not images:
        return "skip:no_image"
    # Dedup by slack_ts
    existing = con.execute(
        "SELECT id FROM background_refs WHERE slack_ts=?", (ts,),
    ).fetchone()
    if existing:
        return "skip:already_ingested"
    if dry_run:
        return f"would_ingest:{len(images)}_images"
    inserted = 0
    for img in images:
        local = download_file(client, img, BG_DIR, prefix="bg")
        if not local:
            continue
        space_name = img.get("title") or (img.get("name") or "").rsplit(".", 1)[0]
        con.execute(
            "INSERT INTO background_refs (file_path, space_name, description, slack_ts) "
            "VALUES (?, ?, ?, ?)",
            (str(local), space_name, text, ts),
        )
        inserted += 1
    con.commit()
    return f"inserted:{inserted}"


def ingest_episode(con: sqlite3.Connection, client, msg: dict,
                    dry_run: bool = False) -> str:
    """#episode — text story seed → episode_stories."""
    ts = msg.get("ts", "")
    text = (msg.get("text") or "").strip()
    if not text or len(text) < 8:
        return "skip:too_short"
    existing = con.execute(
        "SELECT id FROM episode_stories WHERE slack_ts=?", (ts,),
    ).fetchone() if _has_slack_ts_col(con, "episode_stories") else None
    if existing:
        return "skip:already_ingested"
    if dry_run:
        return "would_ingest"
    if _has_slack_ts_col(con, "episode_stories"):
        con.execute(
            "INSERT INTO episode_stories (text, slack_ts) VALUES (?, ?)",
            (text, ts),
        )
    else:
        # Schema lacks slack_ts — fall back to text-only insert with dedup
        already = con.execute(
            "SELECT id FROM episode_stories WHERE text=?", (text,),
        ).fetchone()
        if already:
            return "skip:already_ingested"
        con.execute("INSERT INTO episode_stories (text) VALUES (?)", (text,))
    con.commit()
    return "inserted"


def ingest_media(con: sqlite3.Connection, client, msg: dict,
                  dry_run: bool = False) -> str:
    """#photos — raw photo/video → assets (queued for VLM tagging)."""
    ts = msg.get("ts", "")
    files = msg.get("files") or []
    media = [f for f in files
             if (f.get("mimetype") or "").startswith(("image/", "video/"))]
    if not media:
        return "skip:no_media"
    inserted = 0
    upload_dt = datetime.fromtimestamp(float(ts or 0), tz=timezone.utc)
    for f in media:
        mime = f.get("mimetype") or ""
        kind = "photo" if mime.startswith("image/") else "video"
        # Content-stable dedup key = hash of the Slack file id (matches the proven
        # med_<date>_slack_<hash> scheme so the same file never double-ingests, even
        # though the final asset_id also encodes the capture date resolved below).
        fhash = hashlib.sha1((f.get("id", "x") or "x").encode()).hexdigest()[:8]
        existing = con.execute(
            "SELECT asset_id FROM assets WHERE asset_id LIKE ?", (f"%_slack_{fhash}",),
        ).fetchone()
        if existing:
            continue
        if dry_run:
            inserted += 1
            continue
        # Download first — the real capture date lives in the FILE's metadata, not in
        # Slack (which only knows the upload time). Bucket on disk by upload year for now.
        local = download_file(client, f, target_dir=(PHOTOS_DIR if kind == "photo"
                              else CLIPS_DIR) / upload_dt.strftime("%Y"),
                              prefix=f"slack_{upload_dt.strftime('%Y')}")
        if not local:
            continue
        # PD 2026-06-17: stamp the TRUE capture date so Slack-shared (esp. grandparent)
        # footage clusters by when it was SHOT, not uploaded — else it lands with no date
        # and is invisible to date-based outings / memory-lane. Fall back to the Slack
        # upload time only when the file carries no capture metadata.
        captured = extract_captured_at(local) or upload_dt
        asset_id = f"med_{captured.strftime('%Y_%m_%d_%H%M%S')}_slack_{fhash}"
        try:
            rel = local.relative_to(ROOT)
        except ValueError:
            rel = local
        # PD 2026-06-30: CAPTURE the message text the owner posted WITH the clip — it's
        # the grandmompapa human description of WHAT this is ("하비가 터그 놀이 하는 거야").
        # Dropping it (as before) left only the VLM's guess, which mis-tagged a grandpa-tug
        # clip as "강아지가 돌아다니는 중" → the caption was wrong. Stored in pd_notes and
        # treated as the TOP content-grounding authority (producer._extra_vlm), above the VLM.
        human_desc = (msg.get("text") or "").strip()
        con.execute(
            "INSERT INTO assets (asset_id, source, kind, file_path, captured_iso, "
            "ingested_iso, pd_notes) VALUES (?, 'slack', ?, ?, ?, datetime('now'), ?)",
            (asset_id, kind, str(rel), captured.isoformat(), human_desc or None),
        )
        inserted += 1
    if not dry_run:
        con.commit()
    return f"queued_for_vlm:{inserted}" if inserted else "skip:nothing_new"


def _has_slack_ts_col(con: sqlite3.Connection, table: str) -> bool:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    return "slack_ts" in cols


HANDLERS = {
    "ingest_background": ingest_background,
    "ingest_episode": ingest_episode,
    "ingest_media": ingest_media,
}


def sync_channel(con: sqlite3.Connection, client, name: str,
                  channel_id: str, since: str | None = None,
                  dry_run: bool = False) -> dict[str, int]:
    """Pull new messages from one channel, route to its handler."""
    route = CHANNEL_ROUTES[name]
    handler = HANDLERS[route["handler"]]
    oldest = since if since is not None else get_last_ts(con, channel_id)
    log.info("sync %s (channel=%s, oldest=%s)", name, channel_id, oldest)
    msgs = fetch_messages(client, channel_id, oldest)
    stats: dict[str, int] = {"total": len(msgs)}
    latest_ts = oldest
    for m in msgs:
        ts = m.get("ts", "")
        if ts > latest_ts:
            latest_ts = ts
        try:
            result = handler(con, client, m, dry_run=dry_run)
        except Exception as e:
            log.exception("handler error on ts=%s", ts)
            result = f"error:{type(e).__name__}"
        key = result.split(":")[0]
        stats[key] = stats.get(key, 0) + 1
    if not dry_run and latest_ts > oldest:
        set_last_ts(con, channel_id, name, latest_ts)
    return stats


def backfill_captured_dates(con: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Fill captured_iso on Slack-ingested assets that lack it, from each file's OWN
    capture metadata (EXIF / ffprobe). Earlier #photos ingests stored no capture date,
    so the footage was invisible to date-based outings / memory-lane. Only captured_iso
    is set — asset_ids stay as-is (other rows reference them)."""
    rows = con.execute(
        "SELECT asset_id, file_path FROM assets WHERE source='slack' "
        "AND (captured_iso IS NULL OR captured_iso='')").fetchall()
    found = 0
    for r in rows:
        fp = r["file_path"]
        p = Path(fp) if os.path.isabs(fp) else (ROOT / fp)
        if not p.exists():
            continue
        cap = extract_captured_at(p)
        if not cap:
            continue
        if not dry_run:
            con.execute("UPDATE assets SET captured_iso=? WHERE asset_id=?",
                        (cap.isoformat(), r["asset_id"]))
        found += 1
    if not dry_run:
        con.commit()
    return {"candidates": len(rows), "backfilled": found}


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--channel", choices=list(CHANNEL_ROUTES.keys()),
                   action="append", default=[],
                   help="sync only this channel (repeatable)")
    p.add_argument("--since", help="override last_ts (ISO date or unix ts)")
    p.add_argument("--bootstrap", action="store_true",
                   help="ingest from beginning (sets oldest=0)")
    p.add_argument("--backfill-dates", action="store_true",
                   help="fill captured_iso on Slack assets missing it (from file "
                        "metadata) and exit — no channel sync")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.backfill_dates:
        con = _db()
        res = backfill_captured_dates(con, dry_run=args.dry_run)
        print(f"backfill captured_iso: {res}")
        return 0

    since = None
    if args.bootstrap:
        since = "0"
    elif args.since:
        # If looks like a date, convert; otherwise pass through
        if "-" in args.since and len(args.since) >= 10:
            try:
                dt = datetime.strptime(args.since[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                since = str(dt.timestamp())
            except ValueError:
                since = args.since
        else:
            since = args.since

    targets = args.channel or list(CHANNEL_ROUTES.keys())
    con = _db()
    client = _client()

    overall: dict[str, dict[str, int]] = {}
    for name in targets:
        env_var = CHANNEL_ROUTES[name]["env"]
        channel_id = os.environ.get(env_var)
        if not channel_id:
            log.warning("%s not set — skip", env_var)
            continue
        try:
            overall[name] = sync_channel(
                con, client, name, channel_id, since=since, dry_run=args.dry_run
            )
        except Exception as e:
            log.exception("sync_channel %s failed", name)
            overall[name] = {"error": str(e)[:120]}

    print("\n=== Slack sync result ===")
    for name, stats in overall.items():
        print(f"  {name}: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
