#!/usr/bin/env python3
"""Session-start shared-loop context: recent progress log + open board escalations.

Prints a plain-text block for the SessionStart hook so a CLI session begins on top
of what board (Slack executor) has done. See CLAUDE.md "board↔CLI 공유 루프 RULE".
Fail-safe: any error prints nothing (the handoff still loads).
"""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _recent_progress() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from agents.progress_log import recent_progress
        return recent_progress(15)
    except Exception:
        return ""


def _open_escalations() -> list:
    db_path = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db")))
    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(str(db_path), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT id, summary, ts FROM board_escalations WHERE handled=0 "
                "ORDER BY id ASC LIMIT 10").fetchall()
        finally:
            con.close()
    except Exception:
        return []


def main() -> None:
    out = []
    prog = _recent_progress().strip()
    _sentinels = {"(진행 로그 없음)", "(진행 로그 비어있음)", "(진행 로그 읽기 실패)"}
    if prog and prog not in _sentinels:
        out.append("## board↔CLI 공유 진행 로그 (최근 15)\n" + prog)
    esc = _open_escalations()
    if esc:
        lines = ["## board_escalations — CLI 인계 대기 (handled=0)"]
        for e in esc:
            lines.append(f"- #{e['id']} {(e['summary'] or '')[:120]}")
        out.append("\n".join(lines))
    if out:
        print("\n\n".join(out))


if __name__ == "__main__":
    main()
