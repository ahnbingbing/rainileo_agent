"""agents/notify.py — quality/ops WARNINGS to a dedicated Slack channel.

PD 2026-10-01: keep warnings (a skipped-heal marking flag, an empty slot, a cost anomaly)
OUT of the noisy per-slot progress stream AND the review summary — route them to ONE
dedicated warnings channel PD can scan at a glance. The per-slot threads stay for humans
who want the play-by-play; the review summary stays for the videos; warnings live on their
own so nothing important drowns in render step-spam.

Channel resolution: SLACK_WARN_CHANNEL → SLACK_BOARD_CHANNEL → SLACK_WORKROOM_CHANNEL →
SLACK_CHANNEL. PD 2026-10-01 routes warnings to the BOARD channel by default (board is the
ops/automation channel PD watches); set SLACK_WARN_CHANNEL to override to a dedicated one.
Best-effort: never raises (a warning must never fail a render/upload).
"""
import logging
import os

log = logging.getLogger(__name__)


def _warn_channel() -> str | None:
    for k in ("SLACK_WARN_CHANNEL", "SLACK_BOARD_CHANNEL",
              "SLACK_WORKROOM_CHANNEL", "SLACK_CHANNEL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def warn(text: str, *, thread_ts: str | None = None) -> None:
    """Post one warning line to the warnings channel. No-op (logs) if Slack unconfigured."""
    post(f":warning: {text}", thread_ts=thread_ts)


def post(text: str, *, thread_ts: str | None = None) -> None:
    """Post one plain line to the board/warnings channel (no :warning: prefix). Same channel
    resolution as warn — for ops digests (e.g. the comment-agent approval queue) that aren't
    warnings. No-op (logs) if Slack unconfigured. Best-effort: never raises."""
    ch = _warn_channel()
    tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not ch or not tok:
        log.info("notify.post (no slack): %s", text)
        return
    try:
        from slack_sdk import WebClient
        WebClient(token=tok).chat_postMessage(channel=ch, thread_ts=thread_ts, text=text[:3900])
    except Exception as e:
        log.warning("notify.post failed: %s", str(e)[:120])
