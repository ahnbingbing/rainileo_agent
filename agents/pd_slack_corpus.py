"""Collect PD's review-thread comments from Slack into the pd_taste corpus (PD-mirror P1).

P0 already captures PD's VETOES with reasons. But PD also leaves nuance in the mp4-review threads
that never becomes a veto — "이건 좋은데 캡션만 어색", "톤이 너무 설교조", a praise that marks what to
KEEP. That reasoning is the richest PD-utterance source (grounding_pd_notes_gpt4omini: pd_notes/
PD's own words beat any synthesis), and Giri should learn from it. This module reads the board
channel's launch threads (thread_ts → episode via launch_threads) and appends PD's human comments
to pd_selections so taste_digest surfaces them to both the generators and Giri.

★ STATUS: NEEDS LIVE-SLACK SMOKE. This is the first conversations.replies reader in the repo — it
depends on the bot having history scopes (channels:history / groups:history) and on correctly
telling PD's messages from the bot's. It is OFF/opt-in (never runs in any pipeline); validate it
against the real board channel on the VM before trusting its output, then wire a weekly cron.
Best-effort throughout: any Slack/permission failure returns [] and writes nothing.

    python -m agents.pd_slack_corpus --days 7            # dry: print what it would ingest
    python -m agents.pd_slack_corpus --days 7 --apply    # append to pd_selections
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import datetime as dt
from pathlib import Path
import logging

log = logging.getLogger("agents.pd_slack_corpus")
ROOT = Path(__file__).resolve().parent.parent


def _channel() -> str | None:
    for k in ("SLACK_BOARD_CHANNEL", "SLACK_WORKROOM_CHANNEL", "SLACK_CHANNEL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def _is_pd_message(m: dict) -> bool:
    """A human PD message, not the bot. Bot posts carry bot_id/app_id; join/system posts carry a
    subtype. If PD_SLACK_USER_ID is set, require it (the tightest filter)."""
    if m.get("bot_id") or m.get("app_id") or m.get("subtype"):
        return False
    pd_uid = (os.environ.get("PD_SLACK_USER_ID") or "").strip()
    if pd_uid:
        return m.get("user") == pd_uid
    return bool(m.get("user")) and bool((m.get("text") or "").strip())


_FEEDBACK_HINTS = ("캡션", "자막", "톤", "제목", "편집", "느낌", "어색", "별로", "좋", "싫",
                   "이상", "안 맞", "안맞", "근본", "다시", "바꿔", "왜", "컨셉", "너무")


def _looks_like_feedback(text: str) -> bool:
    """Keep substantive feedback, drop chatter/acks. A veto reply is already logged by _do_veto, so
    skip bare veto words here to avoid double-counting."""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if t.split()[0].lower() in ("veto", "베토", "비토", "거부", "ok", "ㅇㅋ", "👍"):
        return False
    return any(h in t for h in _FEEDBACK_HINTS)


def collect(con: sqlite3.Connection, *, days: int = 7, apply: bool = False) -> list[dict]:
    """Read PD's feedback comments from recent launch-thread replies. Returns the collected
    comments [{video_id, lane, title, text, ts}]; appends to pd_selections when apply=True."""
    ch = _channel()
    if not ch:
        log.warning("pd_slack_corpus: no board channel configured")
        return []
    try:
        from slack_sdk import WebClient
        tok = os.environ.get("SLACK_BOT_TOKEN")
        if not tok:
            log.warning("pd_slack_corpus: no SLACK_BOT_TOKEN")
            return []
        client = WebClient(token=tok)
    except Exception as e:
        log.warning("pd_slack_corpus: slack_sdk unavailable: %s", e)
        return []

    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    con.row_factory = sqlite3.Row
    try:
        threads = con.execute(
            "SELECT thread_ts, video_id, lane, title FROM launch_threads "
            "WHERE target >= ? AND thread_ts IS NOT NULL ORDER BY target DESC",
            (since,)).fetchall()
    except Exception as e:
        log.warning("pd_slack_corpus: launch_threads read failed: %s", e)
        return []

    # Diagnostics so "collected 0" is never ambiguous: distinguish "no threads / no comments" from
    # "the bot can't read" (missing conversations:history scope → every replies call errors).
    diag = {"threads": len(threads), "reply_errors": 0, "first_error": "",
            "pd_msgs": 0, "fed": 0}
    out: list[dict] = []
    for th in threads:
        try:
            resp = client.conversations_replies(channel=ch, ts=th["thread_ts"], limit=50)
            msgs = resp.get("messages") or []
        except Exception as e:
            diag["reply_errors"] += 1
            if not diag["first_error"]:
                diag["first_error"] = str(e)[:160]
            log.debug("replies fetch failed for %s: %s", th["thread_ts"], e)
            continue
        for m in msgs:
            if not _is_pd_message(m):
                continue
            diag["pd_msgs"] += 1
            text = (m.get("text") or "").strip()
            if not _looks_like_feedback(text):
                continue
            diag["fed"] += 1
            out.append({"video_id": th["video_id"], "lane": th["lane"],
                        "title": th["title"], "text": text, "ts": m.get("ts")})
    globals()["_LAST_DIAG"] = diag
    if not out:
        log.info("pd_slack_corpus diag: threads=%d reply_errors=%d pd_msgs=%d%s",
                 diag["threads"], diag["reply_errors"], diag["pd_msgs"],
                 (f" first_error={diag['first_error']}" if diag["first_error"] else ""))

    if apply and out:
        try:
            from agents import pd_taste as _pt
            # Dedup against what's already stored (by the exact reason text).
            _pt.ensure_table(con)
            seen = {r[0] for r in con.execute(
                "SELECT reason FROM pd_selections WHERE reason LIKE 'PD 리뷰코멘트%'").fetchall()}
            n = 0
            for c in out:
                reason = f"PD 리뷰코멘트 '{(c['title'] or c['video_id'] or '')[:30]}': {c['text']}"
                if reason in seen:
                    continue
                # A review comment is a correction-grade signal; kind=caption is the most common
                # target, but keep it generic-concept so the digest shows it under the right head.
                _pt.log_selection(con, lane=(c["lane"] or "both"), kind=_pt.K_CAPTION,
                                  decision=_pt.CORRECT, subject=c["video_id"] or "",
                                  reason=reason)
                seen.add(reason)
                n += 1
            log.info("pd_slack_corpus: ingested %d PD comments", n)
        except Exception as e:
            log.warning("pd_slack_corpus: ingest failed: %s", e)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(ROOT / "data" / "agent.db")
    rows = collect(con, days=a.days, apply=a.apply)
    con.close()
    print(f"collected {len(rows)} PD feedback comment(s){' (applied)' if a.apply else ' (dry)'}")
    d = globals().get("_LAST_DIAG") or {}
    if d:
        print(f"  diag: threads={d.get('threads')} reply_errors={d.get('reply_errors')} "
              f"pd_msgs={d.get('pd_msgs')} fed={d.get('fed')}")
        if d.get("first_error"):
            print(f"  ⚠ replies error (likely a Slack scope issue — needs "
                  f"conversations:history / groups:history): {d['first_error']}")
        elif d.get("threads") and not d.get("pd_msgs"):
            print("  → threads read OK, but no PD (human) messages in them "
                  "(PD leaves feedback elsewhere — this net is for future thread comments).")
    for r in rows[:20]:
        print(f"  [{r['lane']}] {(r['title'] or '')[:24]} — {r['text'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
