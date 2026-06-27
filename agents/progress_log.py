"""Shared board ↔ CLI progress log (PD 2026-06-27).

A single human-readable log that BOTH the board autonomous executor and the CLI
(Claude Code) append to and read — so they work in the SAME context instead of in
parallel silos. One line per meaningful action: who did what, when.

- `log_progress(actor, summary)` — append one entry. actor = 'board' | 'CLI'.
- `recent_progress(n)` — last n entries, for context injection (board agent) and
  session start (CLI).

Lives in notes/progress_log.md so it's git-tracked and human-readable alongside
the session handoff notes.
"""
from __future__ import annotations
from pathlib import Path
import datetime as dt

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "notes" / "progress_log.md"


def log_progress(actor: str, summary: str) -> None:
    """Append one progress entry. actor: 'board' (autonomous executor) or 'CLI'
    (Claude Code session). summary: a one-line description of what was done."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        actor = (actor or "?").strip()
        line = f"- `{ts}` **[{actor}]** {summary.strip()}\n"
        if not LOG.exists():
            LOG.write_text("# 진행 로그 (board ↔ CLI 공용)\n\n"
                           "board 자율 executor와 CLI(Claude Code)가 함께 쓰고 읽는 단일 진행 기록.\n"
                           "서로 무엇을 했는지 같은 맥락에서 이어가기 위함. 한 줄 = 한 작업.\n\n", encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # progress log is best-effort; never break the caller


def recent_progress(n: int = 15) -> str:
    """Last n entries as text. For board-agent context injection + CLI session start."""
    try:
        if not LOG.exists():
            return "(진행 로그 없음)"
        entries = [l for l in LOG.read_text(encoding="utf-8").splitlines()
                   if l.strip().startswith("- `")]
        return "\n".join(entries[-max(1, n):]) or "(진행 로그 비어있음)"
    except Exception:
        return "(진행 로그 읽기 실패)"
