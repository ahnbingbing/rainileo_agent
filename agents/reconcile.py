"""Reconcile YouTube's live scheduled state against the pipeline DB.

Root motive (2026-07-22): a re-render/re-upload used to overwrite a card's
youtube_video_id WITHOUT un-scheduling the old video, leaving ORPHANS — videos
that are private+publishAt on YouTube but have NO card referencing them. Orphans
silently double-book their slot on the public schedule (the 07-23 duplicate slots).

`agents/producer.py:_auto_upload_episode` now vetoes the superseded id on replace,
so new orphans shouldn't appear. This module is the SAFETY NET + auditor: it lists
what YouTube will actually publish and flags anything the DB doesn't know about.

    python -m agents.reconcile                # report orphans (read-only)
    python -m agents.reconcile --veto         # + un-schedule (private) each orphan
    python -m agents.reconcile --json         # machine-readable

`orphan_report()` is also called at the end of the 03:00 launch batch so the daily
Slack summary warns PD when the live schedule and the DB disagree.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sqlite3

log = logging.getLogger(__name__)
KST = dt.timezone(dt.timedelta(hours=9))


def _kst(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
            KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return iso or "?"


def list_scheduled_videos(yt=None) -> list[dict]:
    """Every video the channel will auto-publish: privacyStatus=private + publishAt.
    Walks the uploads playlist (paginated) rather than search() so nothing is missed."""
    if yt is None:
        from youtube.oauth import get_youtube
        yt = get_youtube()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    up = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    tok = None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=up,
                                    maxResults=50, pageToken=tok).execute()
        ids += [it["contentDetails"]["videoId"] for it in r.get("items", [])]
        tok = r.get("nextPageToken")
        if not tok or len(ids) >= 200:
            break
    out: list[dict] = []
    for i in range(0, len(ids), 50):
        v = yt.videos().list(part="snippet,status", id=",".join(ids[i:i + 50])).execute()
        for it in v.get("items", []):
            st = it["status"]
            pa = st.get("publishAt")
            if pa and st.get("privacyStatus") == "private":
                out.append({"video_id": it["id"], "publish_at": pa,
                            "title": it["snippet"]["title"]})
    return sorted(out, key=lambda x: x["publish_at"])


def _card_video_ids(con: sqlite3.Connection) -> set[str]:
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
        if "youtube_video_id" not in cols:
            return set()
        return {r[0] for r in con.execute(
            "SELECT youtube_video_id FROM cards WHERE youtube_video_id IS NOT NULL "
            "AND youtube_video_id != ''")}
    except Exception as e:
        log.warning("reconcile: card id read failed: %s", e)
        return set()


def find_orphans(con: sqlite3.Connection | None = None, yt=None) -> list[dict]:
    """Scheduled YouTube videos with NO matching card. These will publicly publish
    yet are invisible to arc/cooldown/veto-by-card."""
    close = False
    if con is None:
        from agents.producer import _db
        con = _db()
        close = True
    try:
        known = _card_video_ids(con)
        return [s for s in list_scheduled_videos(yt) if s["video_id"] not in known]
    finally:
        if close:
            con.close()


def orphan_report(con: sqlite3.Connection | None = None) -> str:
    """One-line-per-orphan summary for the launch batch Slack post. '' when clean."""
    try:
        orphans = find_orphans(con)
    except Exception as e:
        log.warning("reconcile: orphan_report failed: %s", e)
        return ""
    if not orphans:
        return ""
    lines = [f":warning: *예약 고아 {len(orphans)}건* (YouTube 예약됨 · 카드 없음 — "
             f"`python -m agents.reconcile --veto`로 정리):"]
    for o in orphans:
        lines.append(f"  • {_kst(o['publish_at'])}  {o['video_id']}  {o['title'][:34]}")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level="INFO", format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Reconcile YouTube schedule vs DB cards")
    p.add_argument("--veto", action="store_true",
                   help="un-schedule (set private, clear publishAt) each orphan")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()
    orphans = find_orphans()
    if a.json:
        print(json.dumps(orphans, ensure_ascii=False, indent=2))
    else:
        if not orphans:
            print("clean — every scheduled video has a card.")
        else:
            print(f"{len(orphans)} orphan(s) — scheduled on YouTube, no card:")
            for o in orphans:
                print(f"  {_kst(o['publish_at'])}  {o['video_id']}  {o['title'][:44]}")
    if a.veto and orphans:
        from youtube.upload import veto_video
        for o in orphans:
            try:
                veto_video(o["video_id"], delete=False)
                print(f"  vetoed {o['video_id']}")
            except Exception as e:
                print(f"  !! veto failed {o['video_id']}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
