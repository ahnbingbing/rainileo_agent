"""agents/slot_topup.py — daily 09:00 KST safety net for empty slots.

The 03:00 batch produces D+2 and leaves a gutted slot EMPTY rather than shipping junk
(RF face-leak / footage collapse). Root causes get fixed at the source, but a self-heal
that exhausts its rounds still lands the day short. This job is the NET that guarantees
4/4 regardless: every morning it reads the LIVE schedule for the produced window and, for
each still-empty FUTURE slot, re-runs the (now self-correcting) self-heal for exactly that
slot. Two things make the retry likelier to succeed than the 03:00 attempt did:
  • the face gate backfilled has_human=1 on the clips it dropped overnight, so the pool the
    writer now selects from is cleaner (RF_FACE_GATE_BACKFILL);
  • the human-exclusion selection fix keeps a fresh concept off the face-leak trap.
A slot it still can't fill is reported (loudly) for manual curation — never shipped as junk.

CLI:
  python -m agents.slot_topup                 # fill empty future slots in the window
  python -m agents.slot_topup --dry-run        # report gaps, fill nothing
  python -m agents.slot_topup --days 2 --slack  # window size + post summary to Slack
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os

log = logging.getLogger("agents.slot_topup")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv as _load
    _load(os.path.join(ROOT, ".env"))
except Exception:
    pass


def _slot_min(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _nearest_slot(hhmm_min: int, slots: list[str]) -> str:
    return min(slots, key=lambda s: abs(_slot_min(s) - hhmm_min))


def _occupied(day_strs: set[str]) -> set[tuple[str, str]]:
    """(YYYY-MM-DD, 'HH:MM') that already has a video — PUBLIC or SCHEDULED — on the channel.
    Maps each video's publishAt (scheduled) or publishedAt (public) to its KST day + nearest
    timeslot. Fail-open (empty set) so a YouTube hiccup never blocks the fill decision loudly."""
    from agents.launch import TIMESLOTS, KST
    slots = [s.strip() for s in TIMESLOTS if s.strip()]
    out: set[tuple[str, str]] = set()
    try:
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
        for i in range(0, len(ids), 50):
            v = yt.videos().list(part="snippet,status", id=",".join(ids[i:i + 50])).execute()
            for it in v.get("items", []):
                when = it["status"].get("publishAt") or it["snippet"].get("publishedAt")
                if not when:
                    continue
                k = dt.datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(KST)
                d = k.strftime("%Y-%m-%d")
                if d in day_strs:
                    out.add((d, _nearest_slot(k.hour * 60 + k.minute, slots)))
    except Exception as e:
        log.warning("slot_topup: occupancy lookup failed (%s) — treating window as fully occupied "
                    "to avoid double-booking", e)
        # fail-safe: pretend everything is occupied so we DON'T fill on bad data (a false gap
        # would double-book; a missed gap is caught tomorrow). Return a sentinel handled by caller.
        raise
    return out


def find_gaps(days_ahead: int = 2) -> list[dict]:
    """Empty FUTURE slots across [today .. today+days_ahead], each with its assigned lane."""
    from agents.launch import day_assignments, publish_at_for, KST
    today = dt.datetime.now(KST).date()
    now = dt.datetime.now(dt.timezone.utc)
    day_strs = {(today + dt.timedelta(days=o)).isoformat() for o in range(days_ahead + 1)}
    occ = _occupied(day_strs)
    gaps: list[dict] = []
    for off in range(days_ahead + 1):
        d = today + dt.timedelta(days=off)
        for lane, slot in day_assignments(d):
            pub = publish_at_for(d, slot)
            try:
                pub_dt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except Exception:
                pub_dt = now
            if pub_dt <= now:
                continue  # past slot — can't schedule into the past
            if (d.isoformat(), slot) in occ:
                continue  # already filled (public or scheduled)
            gaps.append({"date": d, "lane": lane, "slot": slot, "publish_at": pub})
    return gaps


def run(days_ahead: int = 2, dry_run: bool = False, do_upload: bool = True) -> dict:
    from agents.launch_selfheal import run_with_selfheal
    try:
        gaps = find_gaps(days_ahead)
    except Exception:
        log.warning("slot_topup: aborting (occupancy unavailable) — will retry next run")
        return {"gaps": [], "filled": [], "failed": [], "aborted": True}
    result = {"gaps": [f"{g['date']} {g['slot']} {g['lane']}" for g in gaps],
              "filled": [], "failed": [], "aborted": False}
    if not gaps:
        log.info("slot_topup: no empty future slots in the next %dd — schedule is full", days_ahead)
        return result
    log.warning("slot_topup: %d empty slot(s): %s", len(gaps), result["gaps"])
    if dry_run:
        return result
    for g in gaps:
        tag = f"{g['date']} {g['slot']} {g['lane']}"
        try:
            # single-slot self-heal (now reroll + human-exclusion + face-gate backfill aware)
            res = run_with_selfheal(g["date"], lane_filter=g["lane"], slot_filter=g["slot"],
                                    do_upload=do_upload)
            done = res.get("done") if isinstance(res, dict) else None
            vid = None
            if isinstance(done, dict):
                for v in done.values():
                    vid = (v or {}).get("video_id") or vid
            if vid:
                result["filled"].append(f"{tag} → {vid}")
                log.warning("slot_topup: FILLED %s → %s", tag, vid)
            else:
                result["failed"].append(tag)
                log.warning("slot_topup: still empty after self-heal: %s (manual curation)", tag)
        except Exception as e:
            log.exception("slot_topup: fill failed %s", tag)
            result["failed"].append(f"{tag} ({e})")
    return result


def _post_slack(result: dict) -> None:
    ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
    if not ch:
        return
    lines = [":sunrise: *09시 슬롯 topup*"]
    if result.get("aborted"):
        lines.append("⚠️ 스케줄 조회 실패로 중단 — 다음 실행에서 재시도")
    elif not result["gaps"]:
        lines.append("빈 슬롯 없음 — 스케줄 꽉 참 ✅")
    else:
        if result["filled"]:
            lines.append("*채움:*\n" + "\n".join(f"  ✅ {x}" for x in result["filled"]))
        if result["failed"]:
            lines.append("*여전히 빔 (손큐레이션 필요):*\n" + "\n".join(f"  ❌ {x}" for x in result["failed"]))
    try:
        from slack_sdk import WebClient
        WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(channel=ch, text="\n".join(lines))
    except Exception as e:
        log.warning("slot_topup: slack post failed: %s", e)


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="daily 09:00 empty-slot topup")
    ap.add_argument("--days", type=int, default=int(os.getenv("TOPUP_DAYS_AHEAD", "2")),
                    help="fill empty slots across today..today+DAYS (default 2 = the produced window)")
    ap.add_argument("--dry-run", action="store_true", help="report gaps, fill nothing")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--slack", action="store_true", help="post summary to SLACK_WORKROOM_CHANNEL")
    a = ap.parse_args()
    result = run(days_ahead=a.days, dry_run=a.dry_run, do_upload=not a.no_upload)
    print("gaps:", result["gaps"])
    print("filled:", result["filled"])
    print("failed:", result["failed"])
    if a.slack:
        _post_slack(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
