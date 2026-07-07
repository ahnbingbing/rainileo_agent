"""board_progress — stream a detached render's milestones back into the PD's board
thread (roadmap B).

A board `rerender` spawns the render as a detached subprocess whose progress goes to
the workroom slot-thread, NOT to the board thread where PD asked — so from PD's side
the job went silent between "시작했어요" and the final result. When _act_rerender sets
BOARD_PROGRESS_CHANNEL + BOARD_PROGRESS_THREAD on the subprocess env, the render posts
its KEY milestones (not every log line) into that exact thread.

Fail-safe: any error posting is swallowed — progress streaming must never break a render.
"""
from __future__ import annotations

import os

# Milestone markers worth surfacing to PD (avoid spamming the thread with every log line).
_MILESTONE_HINTS = (
    "재렌더", "캡션 보존", "콘티", "Seedance", "캐릭터 생성", "Burning caption", "캡션",
    "Final assembly", "조립", "Rendered", "예약", "재업로드", "배치 써머리", "실패",
    "기리", "Giri", "완료", "업로드",
)


def _is_milestone(msg: str) -> bool:
    m = msg or ""
    return any(h in m for h in _MILESTONE_HINTS)


def post_board_progress(msg: str, *, force: bool = False) -> None:
    """Post one milestone line to the board thread named by env, if configured.
    `force=True` posts regardless of the milestone filter (use for start/done)."""
    ch = os.environ.get("BOARD_PROGRESS_CHANNEL", "").strip()
    ts = os.environ.get("BOARD_PROGRESS_THREAD", "").strip()
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not (ch and ts and tok):
        return
    if not force and not _is_milestone(msg):
        return
    try:
        from slack_sdk import WebClient
        WebClient(token=tok).chat_postMessage(channel=ch, thread_ts=ts, text=msg[:600])
    except Exception:
        pass
