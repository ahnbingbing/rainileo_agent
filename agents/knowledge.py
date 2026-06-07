"""agents/knowledge.py — learned character/world facts (PD 2026-06-07).

Layer ③ of the character-knowledge system: when concept generation hits a
character/world fact it can't ground (from VLM ① or PD-authored facts ②), it must
NOT invent — it asks PD in the Slack thread, and the answer is stored HERE,
permanently, so it's asked only once. Stored facts are injected back into the arc
+ concept prompts (alongside arc.CHARACTER_FACTS).

This is what would have caught the "랴니 물 공포" hallucination — the planner would
have asked "랴니는 물을 좋아하나요 무서워하나요?" instead of inventing.

Status flow:  pending (asked, awaiting PD)  →  answered (fact stored, injectable)
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger("agents.knowledge")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS character_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            qkey       TEXT UNIQUE,        -- normalized question (dedup)
            subject    TEXT,               -- 랴니 / 레오 / world / etc.
            question   TEXT,
            fact       TEXT,               -- PD's answer (null while pending)
            source     TEXT,               -- 'PD' / 'vlm' / 'inferred'
            status     TEXT,               -- 'pending' | 'answered'
            created_at TEXT,
            answered_at TEXT
        )
        """
    )
    con.commit()


def _qkey(question: str) -> str:
    """Normalize a question for dedup: lowercase, strip punctuation/space."""
    q = (question or "").lower().strip()
    q = re.sub(r"[\s?!.,~·…]+", "", q)
    return q[:200]


def has_question(con: sqlite3.Connection, question: str) -> bool:
    """True if this question was already asked (pending or answered)."""
    ensure_table(con)
    r = con.execute("SELECT 1 FROM character_facts WHERE qkey=?",
                    (_qkey(question),)).fetchone()
    return r is not None


def add_pending(con: sqlite3.Connection, subject: str, question: str) -> bool:
    """Record a new unanswered question. Returns False if already known (dedup)."""
    ensure_table(con)
    if has_question(con, question):
        return False
    con.execute(
        "INSERT OR IGNORE INTO character_facts "
        "(qkey, subject, question, status, source, created_at) "
        "VALUES (?,?,?,'pending','PD',?)",
        (_qkey(question), subject or "", question,
         dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    con.commit()
    return True


def add_answer(con: sqlite3.Connection, question: str, fact: str,
               subject: str = "", source: str = "PD") -> None:
    """Store/Upsert an answered fact (asked once, remembered forever)."""
    ensure_table(con)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        INSERT INTO character_facts
          (qkey, subject, question, fact, source, status, created_at, answered_at)
        VALUES (?,?,?,?,?, 'answered', ?, ?)
        ON CONFLICT(qkey) DO UPDATE SET
          fact=excluded.fact, source=excluded.source, status='answered',
          subject=COALESCE(NULLIF(excluded.subject,''), character_facts.subject),
          answered_at=excluded.answered_at
        """,
        (_qkey(question), subject, question, fact, source, now, now),
    )
    con.commit()


def pending_questions(con: sqlite3.Connection) -> list[dict]:
    ensure_table(con)
    return [dict(r) for r in con.execute(
        "SELECT id, subject, question FROM character_facts "
        "WHERE status='pending' ORDER BY created_at")]


def facts_block(con: sqlite3.Connection) -> str:
    """All answered learned facts as an injectable prompt block (empty if none)."""
    ensure_table(con)
    rows = con.execute(
        "SELECT subject, question, fact FROM character_facts "
        "WHERE status='answered' AND fact IS NOT NULL ORDER BY subject, created_at"
    ).fetchall()
    if not rows:
        return ""
    lines = ["## 학습된 사실 (PD 확인 — 권위, 발명 금지)"]
    for r in rows:
        sub = f"[{r['subject']}] " if r["subject"] else ""
        lines.append(f"- {sub}{r['fact']}")
    return "\n".join(lines) + "\n"


def is_launch_week(today: str) -> bool:
    """Week-1 of launch (blocking Q&A) per LAUNCH_START_DATE. PD 2026-06-07:
    week1 = blocking (knowledge seeding), week2+ = non-blocking + cache."""
    import os
    start = os.getenv("LAUNCH_START_DATE", "").strip()
    if not start:
        return False
    try:
        d0 = dt.date.fromisoformat(start)
        dn = dt.date.fromisoformat(today[:10])
        return 0 <= (dn - d0).days < 7
    except Exception:
        return False


# ── extract knowledge_questions emitted by the concept LLM ──
def collect_questions(concepts: list[dict]) -> list[dict]:
    """Pull knowledge_questions out of concept dicts. Each item: {subject, question}."""
    out: list[dict] = []
    for c in concepts or []:
        for q in (c.get("knowledge_questions") or []):
            if isinstance(q, str) and q.strip():
                out.append({"subject": c.get("subject_focus", ""), "question": q.strip()})
            elif isinstance(q, dict) and q.get("question"):
                out.append({"subject": q.get("subject", ""), "question": q["question"].strip()})
    return out


def main() -> int:
    import argparse, json
    logging.basicConfig(level="INFO")
    ap = argparse.ArgumentParser(description="character/world learned facts")
    ap.add_argument("--list", action="store_true", help="list all facts")
    ap.add_argument("--pending", action="store_true", help="list pending questions")
    ap.add_argument("--add", nargs=2, metavar=("QUESTION", "FACT"),
                    help="store an answered fact")
    ap.add_argument("--subject", default="", help="subject for --add")
    ap.add_argument("--block", action="store_true", help="print injectable facts block")
    args = ap.parse_args()
    con = _db()
    if args.add:
        add_answer(con, args.add[0], args.add[1], subject=args.subject)
        print("stored.")
    if args.pending:
        print(json.dumps(pending_questions(con), ensure_ascii=False, indent=2))
    if args.block:
        print(facts_block(con) or "(no facts)")
    if args.list or not (args.add or args.pending or args.block):
        rows = [dict(r) for r in con.execute(
            "SELECT subject, status, question, fact FROM character_facts ORDER BY subject")]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
