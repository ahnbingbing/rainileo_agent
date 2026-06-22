#!/usr/bin/env python3
"""Autonomous pickup for rayleo_board CLI escalations (PD 2026-06-22).

The board bot (slack/board_agent.py) queues anything repo-level/ambiguous into the
`board_escalations` table and tells PD "a CLI session will handle it". That was an
empty promise — nothing consumed the queue. This is the consumer: a launchd job runs
it on an interval; for each unhandled escalation it spawns a HEADLESS Claude Code in
this repo, restricted to READ-ONLY tools, to investigate and write a concise Korean
analysis + concrete proposed fix, posts that back to the board, and marks it handled.

Safety, by construction:
  • --allowedTools Read,Grep,Glob → Claude can read/search but has NO Edit/Write/Bash
    tool at all, so it physically cannot change code, commit, or run a mutating
    command. The picker only ever PRODUCES ANALYSIS; actual fixes stay human-gated
    (PD reads the analysis and tells a real interactive session to implement).
  • Bounded: at most MAX_PER_RUN escalations/run, each with a hard timeout.
  • Opt-in kill switch: BOARD_PICKER_ENABLED=0 disables.
  • Single-flight lock so overlapping launchd fires don't double-process.

Run:  .venv/bin/python -m scripts.process_board_escalations
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
LOCK = ROOT / "data" / "tmp" / "board_picker.lock"
MAX_PER_RUN = int(os.getenv("BOARD_PICKER_MAX", "3"))
CLAUDE_TIMEOUT_S = int(os.getenv("BOARD_PICKER_TIMEOUT_S", "900"))  # 15 min/escalation
CLAUDE_BIN = os.getenv("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS board_escalations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now')), "
        "author TEXT, request TEXT, summary TEXT, handled INTEGER DEFAULT 0)")
    # result column added lazily (older table won't have it)
    cols = [r[1] for r in con.execute("PRAGMA table_info(board_escalations)").fetchall()]
    if "result" not in cols:
        con.execute("ALTER TABLE board_escalations ADD COLUMN result TEXT")
    con.commit()
    return con


def _post_board(text: str) -> None:
    """Post a message to the rayleo_board channel."""
    chan = os.getenv("SLACK_BOARD_CHANNEL")
    tok = os.getenv("SLACK_BOT_TOKEN")
    if not chan or not tok:
        print("no SLACK_BOARD_CHANNEL/SLACK_BOT_TOKEN — skipping post", file=sys.stderr)
        return
    try:
        from slack_sdk import WebClient
        WebClient(token=tok).chat_postMessage(channel=chan, text=text, unfurl_links=False)
    except Exception as e:  # noqa: BLE001
        print(f"board post failed: {e}", file=sys.stderr)


_PROMPT = """\
너는 'Ryani(랴니=프렌치불독, 꼬리 없음)와 Leo(레오=주황 고양이, 2025-10 입양)' 펫 YouTube
Shorts 파이프라인 레포의 CLI 분석가다. PD가 Slack rayleo_board에서 다음을 요청했고, 너에게
넘어왔다:

  요청: {request}

이 레포(현재 디렉토리)를 **읽기 전용으로** 조사해서 원인/배경을 분석하라. CLAUDE.md와
관련 코드·데이터를 직접 확인하고 추측하지 마라. 그리고 **한국어 존댓말로 간결하게** 답하라:

1) 결론 한 줄.
2) 근거 (어디서 확인했는지 file:line / 데이터).
3) 제안하는 수정 (구체적으로 어떤 파일/단계를 어떻게 — 하지만 너는 분석만 하고 직접
   고치지는 않는다. PD가 보고 실제 수정은 인터랙티브 세션에 맡긴다).

Slack 메시지로 그대로 게시될 거라 마크다운 과하지 않게, 12줄 이내로 써라."""


def _run_claude(request: str) -> str:
    """Headless Claude Code, READ-ONLY → returns its analysis text.

    --allowedTools limits it to Read/Grep/Glob: it can investigate the repo but
    physically cannot Edit/Write/Bash, so the autonomous run can't change code, commit,
    or run a mutating command. (PLAN mode was tried first but its headless stdout is
    plan-meta, not the analysis, and it wrote a stray plan file — the tool whitelist is
    both cleaner and stricter.) --output-format json gives a stable `result` field."""
    import json as _json
    prompt = _PROMPT.format(request=request[:1500])
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--allowedTools", "Read,Grep,Glob", "--output-format", "json"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return ":hourglass: (분석이 시간초과됐어요 — 요청이 너무 크면 쪼개서 다시 올려주세요.)"
    except FileNotFoundError:
        return f":x: (claude CLI를 못 찾음: {CLAUDE_BIN})"
    try:
        data = _json.loads(proc.stdout or "{}")
    except _json.JSONDecodeError:
        out = (proc.stdout or "").strip()
        return out or f":x: (분석 출력 파싱 실패. stderr: {(proc.stderr or '')[:300]})"
    if data.get("is_error"):
        return f":x: (분석 중 오류: {str(data.get('result') or data.get('api_error_status'))[:300]})"
    out = (data.get("result") or "").strip()
    return out or f":x: (분석 결과가 비었어요. stderr: {(proc.stderr or '')[:300]})"


def main() -> int:
    if os.getenv("BOARD_PICKER_ENABLED", "1") != "1":
        print("BOARD_PICKER_ENABLED != 1 — disabled")
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    # single-flight: O_CREAT|O_EXCL lock file
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError:
        print("another picker run holds the lock — exiting")
        return 0
    try:
        con = _db()
        rows = con.execute(
            "SELECT id, request, summary FROM board_escalations WHERE handled=0 "
            "ORDER BY id ASC LIMIT ?", (MAX_PER_RUN,)).fetchall()
        if not rows:
            print("no open escalations")
            return 0
        print(f"processing {len(rows)} escalation(s)")
        for r in rows:
            eid = r["id"]
            req = r["request"] or r["summary"] or ""
            print(f"--- #{eid}: {req[:80]}")
            analysis = _run_claude(req)
            _post_board(f":robot_face: *CLI 분석 — 요청 `#{eid}`* ({r['summary'] or ''})\n\n{analysis}")
            con.execute("UPDATE board_escalations SET handled=1, result=? WHERE id=?",
                        (analysis[:4000], eid))
            con.commit()
        return 0
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
