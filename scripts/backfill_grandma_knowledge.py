"""Backfill the knowledge layer from grandma/grandpa conversation history.

PD 2026-07-13: the clues 할머니·할아버지 gave in conversation only ever landed in
episode_stories (concept-material queue) — never in character_facts, so they never reached
the caption VLMs (the '멸치' vs '청어' miss). Going forward `_grandma_converse` harvests facts
live; this one-shot backfill mines the EXISTING episode_stories so the accumulated history
isn't lost. LLM-distills durable facts (pet habits/foods/traits, people, objects, places) —
skipping one-off events and concept requests — and stores each via knowledge.remember_fact.

    PYTHONPATH=. .venv/bin/python scripts/backfill_grandma_knowledge.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"

_SYS = (
    "너는 반려동물 채널의 지식 정리 담당이다. 아래는 할머니·할아버지가 시간에 걸쳐 남긴 메모들이다"
    "(펫 랴니=강아지·레오=고양이, 하비=할아버지·함미=할머니). 여기서 **메모에 명시적으로 적힌 지속적 "
    "사실**만 추출해 중복 없이 짧은 표준어 평서문 배열로 정리하라. 포함: 펫의 먹거리·습성·성격·건강, "
    "사물·장소에 대한 항구적 사실(예: '레오는 청어 말린 간식을 좋아한다', '랴니는 관절 영양제를 먹는다').\n"
    "⚠️엄격 규칙(틀린 사실을 만들면 안 됨):\n"
    "- **누가 무엇을 좋아/싫어하는지 절대 추론하거나 바꾸지 마라.** 'A는 X, B는 Y'를 'B는 X'로 뒤집지 "
    "말고, 한쪽만 언급되면 그 한쪽만 적어라(예: '쇠고기 말린 건 랴니만' → '레오는 쇠고기를 좋아한다'로 "
    "쓰면 안 됨).\n"
    "- 메모에 없는 내용은 채우지 마라. 확실치 않으면 넣지 마라.\n"
    "- 이미 자명한 것 제외: '레오는 고양이다/랴니는 강아지다', '하비=할아버지/함미=할머니' 같은 정의는 "
    "빼라(이미 안다). 일회성 사건(오늘 무엇을 했다)·영상 제작 요청/컨셉·인사·잡담도 제외.\n"
    "같은 사실은 한 번만. JSON만: {\"facts\":[{\"subject\":\"랴니|레오|랴니·레오|사람|\",\"fact\":\"...\"}]}"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="cap episode_stories rows read (0=all)")
    a = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT text, author FROM episode_stories ORDER BY created_at").fetchall()
    notes = []
    for r in rows:
        t = re.sub(r"^\[(요청|컨셉)\]\s*", "", (r["text"] or "").strip())
        if t:
            notes.append(t)
    if a.limit:
        notes = notes[:a.limit]
    if not notes:
        print("no episode_stories to mine"); return 0
    print(f"mining {len(notes)} notes…")

    from agents.llm_cascade import call_text_cascade
    facts: list[dict] = []
    # batch to keep each prompt bounded
    B = 60
    for i in range(0, len(notes), B):
        chunk = notes[i:i + B]
        usr = "메모들:\n" + "\n".join(f"- {n}" for n in chunk)
        try:
            raw = call_text_cascade(_SYS, usr, max_tokens=1500).strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw); raw = re.sub(r"\s*```$", "", raw)
            d = json.loads(raw)
            for f in d.get("facts", []):
                if isinstance(f, dict) and (f.get("fact") or "").strip():
                    facts.append({"subject": (f.get("subject") or "").strip(),
                                  "fact": f["fact"].strip()})
        except Exception as e:
            print(f"  batch {i//B} failed: {e}")

    print(f"extracted {len(facts)} candidate facts")
    for f in facts:
        print(f"  [{f['subject']}] {f['fact']}")
    if a.dry_run:
        print("(dry-run — not stored)"); return 0

    from agents import knowledge as _kn
    stored = 0
    for f in facts:
        if _kn.remember_fact(con, f["fact"], subject=f["subject"], source="grandma_backfill"):
            stored += 1
    print(f"stored {stored} new facts (dedup skipped {len(facts) - stored})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
