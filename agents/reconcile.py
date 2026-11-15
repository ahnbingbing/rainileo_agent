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


def relink_orphans(con: sqlite3.Connection | None = None, yt=None,
                   apply: bool = True) -> list[dict]:
    """Root fix (PD 2026-11-12) for the dedup-blindness that let a scheduled-but-ORPHANED
    video's clips slip past the RF reuse gates: bpHO0z shared the 2025-12-25 outing with the
    orphan cExdhHxLUDM because cExdh's source card had NO youtube_video_id — so every gate
    (keyed on video_id / publish_at) was blind to its clips. An orphan is almost always a lost
    UPLOAD WRITEBACK (the card rendered + uploaded, but the video_id never landed on the card).
    If a UNIQUE rendered RF card for the orphan's publish DATE has no video_id, it IS that card:
    write the link back so arc/cooldown/dedup see its clips again. Conservative — only the
    unambiguous one-card-per-date case is auto-linked; ambiguous orphans fall through to the
    veto path (existing behavior). Env RECONCILE_RELINK=0 disables."""
    import os
    import datetime as _dt
    if os.getenv("RECONCILE_RELINK", "1") == "0":
        return []
    close = False
    if con is None:
        from agents.producer import _db
        con = _db()
        close = True
    relinked: list[dict] = []
    try:
        for o in find_orphans(con, yt):
            try:
                pa = _dt.datetime.fromisoformat(o["publish_at"].replace("Z", "+00:00"))
                kst_date = (pa + _dt.timedelta(hours=9)).date().isoformat()
            except Exception:
                continue
            cands = con.execute(
                "SELECT card_id FROM cards WHERE date=? AND render_style='real_footage' "
                "AND (youtube_video_id IS NULL OR youtube_video_id='') "
                "AND state IN ('rendered','published')", (kst_date,)).fetchall()
            if len(cands) != 1:
                log.warning("reconcile relink: orphan %s (%s) has %d candidate card(s) "
                            "— left for veto (no confident match)",
                            o["video_id"], kst_date, len(cands))
                continue
            cid = cands[0][0]
            if apply:
                con.execute(
                    "UPDATE cards SET youtube_video_id=?, uploaded=1, youtube_publish_at=?, "
                    "state='published' WHERE card_id=?",
                    (o["video_id"], o["publish_at"], cid))
                con.commit()
            relinked.append({"video_id": o["video_id"], "card_id": cid, "date": kst_date})
            log.info("reconcile relink: %s -> card %s (%s) — dedup can see its clips again",
                     o["video_id"], cid, kst_date)
    finally:
        if close:
            con.close()
    return relinked


def find_unscheduled(con: sqlite3.Connection | None = None, yt=None,
                     horizon_days: int = 14) -> list[dict]:
    """The INVERSE of an orphan: cards the DB believes are SCHEDULED (uploaded=1, a future
    youtube_publish_at) whose YouTube video is `private` WITHOUT a publishAt — the schedule
    silently never stuck, so the slot will NOT publish although the DB says it will (the
    11/17 18:00 slot that vanished: DB uploaded=1 + publish_at set, YT publishAt=None, no
    veto). DB-knows / YouTube-forgot. Returns [{video_id, card_id, publish_at, title}] for
    upcoming slots only (past = already public or genuinely missed, not repairable here)."""
    close = False
    if con is None:
        from agents.producer import _db
        con = _db()
        close = True
    try:
        now = dt.datetime.now(dt.timezone.utc)
        horizon = now + dt.timedelta(days=horizon_days)
        want: list[tuple] = []
        for cid, vid, pa, theme in con.execute(
                "SELECT card_id, youtube_video_id, youtube_publish_at, theme FROM cards "
                "WHERE youtube_video_id IS NOT NULL AND youtube_video_id!='' "
                "AND uploaded=1 AND youtube_publish_at IS NOT NULL").fetchall():
            try:
                paz = dt.datetime.fromisoformat(pa.replace("Z", "+00:00"))
            except Exception:
                continue
            if now < paz <= horizon:   # only upcoming, not-yet-published slots
                want.append((cid, vid, pa, theme or ""))
        if not want:
            return []
        if yt is None:
            from youtube.oauth import get_youtube
            yt = get_youtube()
        ids = [w[1] for w in want]
        status: dict = {}
        for i in range(0, len(ids), 50):
            v = yt.videos().list(part="status", id=",".join(ids[i:i + 50])).execute()
            for it in v.get("items", []):
                status[it["id"]] = it["status"]
        # A stuck card is only a LOST slot if no OTHER video already carries a publishAt for
        # the same slot time. Superseded re-renders (the old id after a replace) are also
        # private+no-publishAt, but their slot IS covered by the winner — re-asserting their
        # publishAt would DOUBLE-BOOK the slot. Build the covered-slot set from YouTube truth
        # (every video actually scheduled) and skip stuck cards whose slot is already live.
        def _slot(iso: str) -> str:
            # normalize to minute so YT reformatting (sub-second / offset) can't hide a match
            try:
                return dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(
                    dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
            except Exception:
                return iso or ""
        try:
            covered = {_slot(s["publish_at"]) for s in list_scheduled_videos(yt)}
        except Exception as e:  # noqa: BLE001
            log.warning("find_unscheduled: scheduled-set read failed (%s) — "
                        "skipping to avoid double-booking", e)
            return []
        out: list[dict] = []
        for cid, vid, pa, title in want:
            st = status.get(vid)
            if st is None:
                continue  # video gone → orphan/deleted path, not this one
            if st.get("privacyStatus") != "private" or st.get("publishAt"):
                continue  # already public or already scheduled
            if _slot(pa) in covered:
                continue  # slot already filled by another video → this is a superseded dup
            out.append({"video_id": vid, "card_id": cid,
                        "publish_at": pa, "title": title})
        return out
    finally:
        if close:
            con.close()


def reschedule_stuck(con: sqlite3.Connection | None = None, yt=None,
                     apply: bool = True) -> list[dict]:
    """Repair DB-scheduled / YouTube-unscheduled drift by re-asserting each stuck card's
    publishAt so the slot actually airs (reversible — PD can still /veto). Returns the
    slots acted on, each stamped `repaired` True/False. Env RECONCILE_RESCHEDULE=0 off."""
    import os
    if os.getenv("RECONCILE_RESCHEDULE", "1") == "0":
        return []
    try:
        stuck = find_unscheduled(con, yt)
    except Exception as e:  # noqa: BLE001
        log.warning("reconcile: find_unscheduled failed (non-fatal): %s", e)
        return []
    if not stuck or not apply:
        return stuck
    from youtube.upload import ensure_scheduled
    for s in stuck:
        try:
            s["repaired"] = ensure_scheduled(s["video_id"], s["publish_at"], yt=yt)
            if s["repaired"]:
                log.info("reschedule: re-asserted %s publishAt=%s (slot recovered)",
                         s["video_id"], s["publish_at"])
            else:
                log.error("reschedule: %s would not take publishAt=%s — slot still at risk",
                          s["video_id"], s["publish_at"])
        except Exception as e:  # noqa: BLE001
            s["repaired"] = False
            log.warning("reschedule: %s failed: %s", s["video_id"], e)
    return stuck


def orphan_report(con: sqlite3.Connection | None = None) -> str:
    """One-line-per-orphan summary for the launch batch Slack post. '' when clean.
    Re-links recoverable orphans FIRST (so a lost-writeback video becomes visible to dedup
    instead of being reported/vetoed) — only the truly unmatched remain to report."""
    try:
        relink_orphans(con)
    except Exception as e:
        log.warning("reconcile: relink_orphans failed (non-fatal): %s", e)
    lines: list[str] = []
    # Repair the inverse drift FIRST: slots the DB thinks are scheduled but YouTube forgot
    # to schedule (publishAt dropped) → re-assert so they actually air, and tell PD.
    try:
        repaired = reschedule_stuck(con)
        if repaired:
            ok = [r for r in repaired if r.get("repaired")]
            bad = [r for r in repaired if not r.get("repaired")]
            if ok:
                lines.append(f":arrows_counterclockwise: *예약 유실 복구 {len(ok)}건* "
                             f"(DB는 예약인데 YouTube publishAt 누락 → 재예약):")
                for r in ok:
                    lines.append(f"  • {_kst(r['publish_at'])}  {r['video_id']}  {r['title'][:34]}")
            for r in bad:
                lines.append(f":rotating_light: 예약 복구 실패 {r['video_id']} "
                             f"({_kst(r['publish_at'])}) — 수동 확인 필요")
    except Exception as e:
        log.warning("reconcile: reschedule_stuck failed (non-fatal): %s", e)
    try:
        orphans = find_orphans(con)
    except Exception as e:
        log.warning("reconcile: orphan_report failed: %s", e)
        orphans = []
    if orphans:
        lines.append(f":warning: *예약 고아 {len(orphans)}건* (YouTube 예약됨 · 카드 없음 — "
                     f"`python -m agents.reconcile --veto`로 정리):")
        for o in orphans:
            lines.append(f"  • {_kst(o['publish_at'])}  {o['video_id']}  {o['title'][:34]}")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level="INFO", format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Reconcile YouTube schedule vs DB cards")
    p.add_argument("--veto", action="store_true",
                   help="un-schedule (set private, clear publishAt) each orphan")
    p.add_argument("--reschedule", action="store_true",
                   help="repair DB-scheduled / YouTube-unscheduled slots (re-assert publishAt)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()
    if a.reschedule:
        stuck = reschedule_stuck(apply=True)
        if not stuck:
            print("clean — every DB-scheduled slot carries a publishAt on YouTube.")
        else:
            for s in stuck:
                flag = "✓ re-scheduled" if s.get("repaired") else "!! FAILED"
                print(f"  {flag}  {_kst(s['publish_at'])}  {s['video_id']}  {s['title'][:40]}")
        return 0
    _rel = relink_orphans()
    for r in _rel:
        print(f"  relinked {r['video_id']} -> card {r['card_id']} ({r['date']})")
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
