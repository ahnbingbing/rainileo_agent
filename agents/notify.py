"""agents/notify.py — quality/ops WARNINGS to a dedicated Slack channel.

PD 2026-10-01: keep warnings (a skipped-heal marking flag, an empty slot, a cost anomaly)
OUT of the noisy per-slot progress stream AND the review summary — route them to ONE
dedicated warnings channel PD can scan at a glance. The per-slot threads stay for humans
who want the play-by-play; the review summary stays for the videos; warnings live on their
own so nothing important drowns in render step-spam.

Channel resolution: SLACK_WARN_CHANNEL → SLACK_WORKROOM_CHANNEL → SLACK_CHANNEL.
Best-effort: never raises (a warning must never fail a render/upload).
"""
import logging
import os

log = logging.getLogger(__name__)


def _warn_channel() -> str | None:
    for k in ("SLACK_WARN_CHANNEL", "SLACK_WORKROOM_CHANNEL", "SLACK_CHANNEL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def warn(text: str, *, thread_ts: str | None = None) -> None:
    """Post one warning line to the warnings channel. No-op (logs) if Slack unconfigured."""
    ch = _warn_channel()
    tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not ch or not tok:
        log.info("notify.warn (no slack): %s", text)
        return
    try:
        from slack_sdk import WebClient
        WebClient(token=tok).chat_postMessage(
            channel=ch, thread_ts=thread_ts, text=(f":warning: {text}")[:2000])
    except Exception as e:
        log.warning("notify.warn failed: %s", str(e)[:120])
