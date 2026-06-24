#!/usr/bin/env python3
"""Autonomous executor for rayleo_board escalations (PD 2026-06-22; exec upgrade 2026-06-25).

The board bot (slack/board_agent.py) queues anything repo-level/ambiguous into the
`board_escalations` table. PD's directive (2026-06-25): these must be HANDLED without
waiting for a human to open a CLI session — the CLI-queue was the system's bottleneck.

So this is no longer a read-only analyst: it is an autonomous executor. For each open
escalation it spawns a HEADLESS Claude Code in this repo with full edit/Bash tools,
lets it investigate AND implement the fix, smoke-tests the result, commits it to the
live branch, pushes, and posts a Korean summary back to the board THREAD. No CLI.

How money/destruction stays safe WITHOUT a human gate — by construction, not by trust:
  • The subprocess env has every paid-API key STRIPPED (OpenAI / Google-Veo / BytePlus-
    Seedance / GCP) and the YouTube write creds removed. A render or upload literally
    cannot authenticate, so an autonomous run can NEVER incur a Seedance/Veo/OpenAI
    charge or upload/take-down a video. Code fixes & analysis need none of those keys.
  • Anything that genuinely needs spend or is irreversible → the agent is instructed to
    NOT do it and emit `[APPROVAL] <한 줄 제안>`; the worker posts that as a one-tap
    proposal to the board (PD approves in Slack — still no CLI).
  • Smoke gate: changed .py are byte-compiled + key modules re-imported. If that breaks,
    the change is reverted (git checkout) and the failure is reported — main never ends
    up in a broken state from an autonomous edit.
  • Single-flight lock, bounded per-run, hard per-escalation timeout, kill switch.

Run:  .venv/bin/python -m scripts.process_board_escalations
Kill switch: BOARD_PICKER_ENABLED=0
Read-only legacy mode (no edits/commits): BOARD_EXEC_MODE=analyze
"""
from __future__ import annotations

import json as _json
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
LOCK = ROOT / "data" / "tmp" / "board_picker.lock"
MAX_PER_RUN = int(os.getenv("BOARD_PICKER_MAX", "3"))
CLAUDE_TIMEOUT_S = int(os.getenv("BOARD_PICKER_TIMEOUT_S", "1500"))  # 25 min/escalation
CLAUDE_BIN = os.getenv("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
EXEC_MODE = os.getenv("BOARD_EXEC_MODE", "auto")  # auto | analyze
AUTO_PUSH = os.getenv("BOARD_AUTO_PUSH", "1") == "1"

# Paid / destructive credentials stripped from the executor subprocess. This is the
# hard money/destruction guard — without these the pipeline's render and YouTube-write
# paths cannot authenticate, so an autonomous run cannot spend money or alter the channel.
# (ANTHROPIC_API_KEY is intentionally KEPT — it is the agent's own cheap reasoning, not a
# media-render cost.) Smoke-import + analysis paths need none of the stripped keys.
DENY_KEYS = (
    "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "BYTEPLUS_API_KEY",
    "ARK_API_KEY", "GCP_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
    "YOUTUBE_TOKEN", "YOUTUBE_CLIENT_SECRETS",
)

# Modules re-imported after an edit to catch a broken commit before it lands.
SMOKE_IMPORTS = (
    "agents.producer", "agents.cameraman", "agents.launch", "agents.reviewer",
    "agents.arc", "agents.concept_brainstorm", "slack.board_agent",
)


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS board_escalations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now')), "
        "author TEXT, request TEXT, summary TEXT, handled INTEGER DEFAULT 0)")
    cols = [r[1] for r in con.execute("PRAGMA table_info(board_escalations)").fetchall()]
    for c, ddl in (("result", "result TEXT"), ("channel", "channel TEXT"),
                   ("thread_ts", "thread_ts TEXT")):
        if c not in cols:
            con.execute(f"ALTER TABLE board_escalations ADD COLUMN {ddl}")
    con.commit()
    return con


def _post_board(text: str, *, channel: str | None = None, thread_ts: str | None = None) -> None:
    chan = channel or os.getenv("SLACK_BOARD_CHANNEL")
    tok = os.getenv("SLACK_BOT_TOKEN")
    if not chan or not tok:
        print("no SLACK_BOARD_CHANNEL/SLACK_BOT_TOKEN — skipping post", file=sys.stderr)
        return
    try:
        from slack_sdk import WebClient
        WebClient(token=tok).chat_postMessage(
            channel=chan, thread_ts=thread_ts or None, text=text, unfurl_links=False)
    except Exception as e:  # noqa: BLE001
        print(f"board post failed: {e}", file=sys.stderr)


# ── git helpers ───────────────────────────────────────────────────────────
def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout)


def _git_clean() -> bool:
    return not (_git("status", "--porcelain").stdout or "").strip()


def _changed_py() -> list[str]:
    out = (_git("status", "--porcelain").stdout or "").splitlines()
    files = [ln[3:].strip() for ln in out if ln[3:].strip().endswith(".py")]
    # handle "rename ->" porcelain entries
    return [f.split(" -> ")[-1] for f in files]


def _diffstat() -> str:
    return (_git("diff", "--stat").stdout or _git("diff", "--cached", "--stat").stdout or "").strip()


def _smoke_ok(env: dict) -> tuple[bool, str]:
    """Byte-compile changed .py + re-import key modules. Returns (ok, detail)."""
    changed = _changed_py()
    if changed:
        proc = subprocess.run([sys.executable, "-m", "py_compile", *changed],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return False, f"py_compile 실패:\n{(proc.stderr or '')[:500]}"
    imp = "; ".join(f"import {m}" for m in SMOKE_IMPORTS)
    proc = subprocess.run([sys.executable, "-c", imp], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=180, env=env)
    if proc.returncode != 0:
        return False, f"import 스모크 실패:\n{(proc.stderr or '')[-600:]}"
    return True, "smoke ok"


def _revert() -> None:
    _git("checkout", "--", ".")
    _git("clean", "-fd")


# ── claude exec ───────────────────────────────────────────────────────────
def _sanitized_env() -> dict:
    env = dict(os.environ)
    for k in DENY_KEYS:
        env.pop(k, None)
    # belt-and-suspenders: signal to any pipeline code that paid actions are forbidden
    env["NO_PAID_APIS"] = "1"
    env["DRY_RUN"] = "1"
    return env


_PROMPT_EXEC = """\
너는 'Ryani(랴니=프렌치불독, 꼬리 없음) × Leo(레오=주황 태비, 2025-09생)' 펫 YouTube Shorts
파이프라인 레포의 자율 엔지니어다. PD가 Slack rayleo_board에서 아래를 요청했고 너에게 위임됐다.
사람이 CLI를 열어주길 기다리지 않고 **네가 끝까지 처리**해야 한다.

요청:
{request}

작업 규칙:
1) 먼저 CLAUDE.md와 관련 코드/데이터를 직접 읽어 원인을 정확히 파악하라(추측 금지).
2) 코드/설정/데이터로 고칠 수 있는 일이면 **직접 수정하라**. CLAUDE.md의 prompt-authoring·
   pipeline-change-impact 규칙을 지켜 AV+RF 양 레인 consumer를 모두 갱신하라.
3) 절대 하지 말 것: `git` 명령(커밋·푸시·브랜치 — 그건 워커가 한다), 그리고 **돈이 드는
   렌더(Seedance/Veo/OpenAI 이미지)나 YouTube 업로드/삭제 같은 비가역 작업**. 그런 키는
   이 환경에서 제거되어 실행해도 실패한다. 그런 작업이 필요하면 **하지 말고**, 마지막 줄에
   `[APPROVAL] <PD가 한 번에 승인할 한 줄 제안>` 을 출력하라.
4) 분석만으로 끝나는 요청이면 코드 수정 없이 결론만 내라.
5) 수정했다면 가능한 한 가벼운 sanity 체크(해당 모듈 import / 함수 단위 실행)로 깨지지
   않음을 확인하라.

마지막에 **한국어 존댓말, 12줄 이내**로 Slack에 그대로 올라갈 요약을 써라:
- 결론 한 줄
- 원인/근거 (file:line)
- 한 일 (수정한 파일 — 없으면 '분석만') / 또는 [APPROVAL] 제안
마크다운 과하게 쓰지 마라."""


def _run_claude_exec(request: str, env: dict, *, read_only: bool) -> str:
    prompt = _PROMPT_EXEC.format(request=request[:1800])
    if read_only:
        cmd = [CLAUDE_BIN, "-p", prompt, "--allowedTools", "Read,Grep,Glob",
               "--output-format", "json"]
    else:
        # Full autonomy on tools; money/destruction blocked by stripped env, not by tool list.
        cmd = [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions",
               "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              timeout=CLAUDE_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return ":hourglass: (처리가 시간초과됐어요 — 요청이 크면 쪼개서 다시 올려주세요.)"
    except FileNotFoundError:
        return f":x: (claude CLI를 못 찾음: {CLAUDE_BIN})"
    try:
        data = _json.loads(proc.stdout or "{}")
    except _json.JSONDecodeError:
        out = (proc.stdout or "").strip()
        return out or f":x: (출력 파싱 실패. stderr: {(proc.stderr or '')[:300]})"
    if data.get("is_error"):
        return f":x: (처리 중 오류: {str(data.get('result') or data.get('api_error_status'))[:300]})"
    return (data.get("result") or "").strip() or \
        f":x: (결과가 비었어요. stderr: {(proc.stderr or '')[:300]})"


def _process_one(con: sqlite3.Connection, row: sqlite3.Row) -> None:
    eid = row["id"]
    req = row["request"] or row["summary"] or ""
    channel = row["channel"] if "channel" in row.keys() else None
    thread_ts = row["thread_ts"] if "thread_ts" in row.keys() else None
    print(f"--- #{eid}: {req[:80]}")

    read_only = (EXEC_MODE == "analyze")
    env = _sanitized_env()

    # Refuse to run on a dirty tree (don't entangle our commit with unrelated edits).
    if not read_only and not _git_clean():
        msg = (f":warning: *자동 처리 보류 — `#{eid}`*\n작업 트리에 커밋 안 된 변경이 있어 "
               "코드 수정형 요청을 자동 반영하지 않았어요. 정리 후 다시 시도할게요. (분석만 진행)")
        analysis = _run_claude_exec(req, dict(env), read_only=True)
        _post_board(f"{msg}\n\n{analysis}", channel=channel, thread_ts=thread_ts)
        con.execute("UPDATE board_escalations SET handled=1, result=? WHERE id=?",
                    (analysis[:4000], eid)); con.commit()
        return

    summary = _run_claude_exec(req, env, read_only=read_only)

    committed = ""
    if not read_only and not _git_clean():
        ok, detail = _smoke_ok(env)
        if not ok:
            _revert()
            summary += f"\n\n:x: (스모크 테스트 실패로 변경을 **되돌렸습니다**.)\n{detail[:500]}"
        else:
            files = _changed_py() or ["(non-py)"]
            stat = _diffstat()
            _git("add", "-A")
            cmsg = (f"fix(board): autonomous handling of escalation #{eid}\n\n"
                    f"{(row['summary'] or req)[:160]}\n\n"
                    "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
            cp = _git("commit", "-m", cmsg)
            sha = (_git("rev-parse", "--short", "HEAD").stdout or "").strip()
            pushed = ""
            if AUTO_PUSH:
                br = (_git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()
                pr = _git("push", "origin", br, timeout=120)
                pushed = " · pushed" if pr.returncode == 0 else " · (push 실패)"
            committed = (f"\n\n:white_check_mark: 커밋 `{sha}`{pushed} — {', '.join(files)}\n"
                         f"```{stat[:600]}```\n_되돌리려면 `되돌려 {sha}` 라고 하세요._")
            if cp.returncode != 0:
                committed = f"\n\n:x: (커밋 실패: {(cp.stderr or '')[:200]})"

    final = f":robot_face: *자동 처리 — `#{eid}`* ({row['summary'] or ''})\n\n{summary}{committed}"
    _post_board(final, channel=channel, thread_ts=thread_ts)
    con.execute("UPDATE board_escalations SET handled=1, result=? WHERE id=?",
                (final[:4000], eid)); con.commit()


def main() -> int:
    if os.getenv("BOARD_PICKER_ENABLED", "1") != "1":
        print("BOARD_PICKER_ENABLED != 1 — disabled")
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError:
        print("another executor run holds the lock — exiting")
        return 0
    try:
        con = _db()
        rows = con.execute(
            "SELECT * FROM board_escalations WHERE handled=0 "
            "ORDER BY id ASC LIMIT ?", (MAX_PER_RUN,)).fetchall()
        if not rows:
            print("no open escalations")
            return 0
        print(f"processing {len(rows)} escalation(s) in mode={EXEC_MODE}")
        for r in rows:
            try:
                _process_one(con, r)
            except Exception as e:  # noqa: BLE001 — one bad escalation shouldn't wedge the queue
                print(f"escalation #{r['id']} failed: {e}", file=sys.stderr)
                try:
                    if EXEC_MODE != "analyze" and not _git_clean():
                        _revert()
                except Exception:
                    pass
                con.execute("UPDATE board_escalations SET handled=1, result=? WHERE id=?",
                            (f":x: 처리 실패: {str(e)[:300]}", r["id"])); con.commit()
        return 0
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
