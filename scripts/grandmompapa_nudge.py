"""
scripts/grandmompapa_nudge.py — proactive grandmompapa messages (PD 2026-06-24).

- morning (09:00 KST): warmly encourage 할머니·할아버지 to film & post today's fun
  pet moments.
- evening (19:00 KST): if NOTHING was posted to the channel today, gently ask whether
  anything fun happened — a check-in, not a nag.

Every message is freshly LLM-generated (standard honorific Korean) and varied — recent
bot nudges are fed back so it never repeats the same wording. Real KST time is injected.

    python scripts/grandmompapa_nudge.py morning
    python scripts/grandmompapa_nudge.py evening
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# launchd runs this as `scripts/grandmompapa_nudge.py` with no PYTHONPATH, so
# `from agents...` raised ModuleNotFoundError → morning/evening both crashed.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _kst_now() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        return dt.datetime.now()


def _client():
    from slack_sdk import WebClient
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def _channel() -> str:
    return os.environ.get("SLACK_GRANDMOMPAPA_CHANNEL", "C0BASN221UL")


def _today_messages(client, channel) -> list[dict]:
    """All of today's (KST) messages."""
    now = _kst_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        resp = client.conversations_history(
            channel=channel, oldest=str(start.timestamp()), limit=50)
        return resp.get("messages", [])
    except Exception:
        return []


def _recent_bot_nudges(client, channel, limit=4) -> list[str]:
    """Recent bot messages so the LLM can avoid repeating wording."""
    try:
        resp = client.conversations_history(channel=channel, limit=40)
    except Exception:
        return []
    out = []
    for m in resp.get("messages", []):
        if m.get("bot_id") and (m.get("text") or "").strip():
            out.append(m["text"].strip())
        if len(out) >= limit:
            break
    return out


def _media_posted_today(client, channel) -> bool:
    """Did the family upload any video/photo today? (PD 2026-06-28: the 7pm nudge fires
    when no FOOTAGE came in — text-only chatter doesn't count, since the point is to get
    a video for the channel.)"""
    for m in _today_messages(client, channel):
        if m.get("bot_id"):
            continue
        if m.get("files"):
            return True
    return False


def _gen(kind: str, recent: list[str]) -> str:
    """LLM-generate a fresh, varied nudge in warm standard Korean."""
    from agents.llm_cascade import call_text_cascade
    now = _kst_now()
    weekday = "월화수목금토일"[now.weekday()] + "요일"
    when = f"{now.strftime('%Y년 %m월 %d일')} {weekday} {now.hour}시"
    if kind == "morning":
        goal = ("따뜻한 아침 인사를 건네며, 오늘 랴니(강아지)와 레오(고양이)의 귀여운 순간을 "
                "'영상으로 찍어서 이 채널에 올려달라'고 다정하게 부탁하는 메시지.")
    else:
        goal = ("저녁 인사. 오늘 이 채널에 영상이 하나도 안 올라왔다. 다그치거나 조르지 말고, "
                "**오늘 영상이 없다는 걸 가볍게 짚어주는** 톤으로 — 바쁘셨나 보다 하고 헤아리며 "
                "은근히 영상을 기다린다는 느낌을 주는 메시지. 예: '오늘 바빴나봐요! 오늘은 올라온 게 "
                "없네요 😊' 정도의 한 문장. 부담스럽게 '꼭 올려주세요' 식으로 청하지는 말 것.")
    avoid = ("\n최근에 이미 이렇게 보냈으니 표현·문장을 확실히 다르게 써라(반복 금지):\n- "
             + "\n- ".join(recent)) if recent else ""
    sys_p = (
        "너는 'Ryani(랴니=강아지)와 Leo(레오=고양이)' 펫 채널의 따뜻한 가족 비서다. "
        "할머니·할아버지(충청도 어르신)께 보내는 짧은 메시지 1개를 쓴다. 깔끔한 표준어 존댓말, "
        "1~2문장, 이모지 1~2개. 부담 없이 다정하게. 매번 새롭고 다른 표현을 써라. "
        f"지금: {when}.\n목적: {goal}{avoid}\n메시지 문장만 출력(따옴표·설명 없이).")
    txt = call_text_cascade(sys_p, "메시지 한 개만.", max_tokens=200).strip()
    return txt.strip('"').strip()


def run(kind: str, dry_run: bool = False) -> str | None:
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env"))
    client, channel = _client(), _channel()
    if kind == "evening" and _media_posted_today(client, channel):
        return None  # a video/photo already came in today → no nudge needed
    msg = _gen(kind, _recent_bot_nudges(client, channel))
    if not dry_run:
        client.chat_postMessage(channel=channel, text=msg)
    return msg


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["morning", "evening"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = run(a.kind, dry_run=a.dry_run)
    print(out if out is not None else f"({a.kind}: skipped — already posted today)")
