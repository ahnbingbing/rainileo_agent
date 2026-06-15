"""PD-taste selection memory (PD 2026-06-15).

PD's insight: "cut/클립/컨셉 선택은 리뷰어가 PD의 과거 선택을 학습해서 해야지." Today the Writer
picks clips arbitrarily (it chose a nap photo over the 46s mukbang video → 풀먹방 drift) and the
concept reviewer ranks by a generic audience lens — neither LEARNS what PD actually keeps, vetoes,
and corrects. This module is the learning substrate:

  1. `pd_selections` table — every PD decision (approve / veto / swap / correct / pick) + the REASON.
  2. `log_selection(...)` — append a decision. Call it whenever PD makes a selection call.
  3. `taste_digest(con, lane)` — compact "PD가 이렇게 골라왔다" brief, injected into the selectors
     (concept ranker + the clip-selection gate) so SELECTION reflects PD's accumulated taste.

The digest is the raw signal (decision + reason, grouped) — faithful and LLM-free. A distilled
principle layer can sit on top later. Backfill seeds it with this session's corrections so the
selectors start from real data, not an empty table.
"""
from __future__ import annotations

import sqlite3
import datetime as dt
import logging

log = logging.getLogger("agents.pd_taste")

# decision vocabulary — what PD did
APPROVE, VETO, SWAP, CORRECT, PICK, REJECT = (
    "approve", "veto", "swap", "correct", "pick", "reject")
# kind — what the decision was about
K_CONCEPT, K_CLIP, K_CUT, K_CAPTION, K_SCHEDULE, K_TONE = (
    "concept", "clip", "cut", "caption", "schedule", "tone")


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS pd_selections (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            lane       TEXT,           -- ai_vtuber | real_footage | both
            kind       TEXT NOT NULL,  -- concept | clip | cut | caption | schedule | tone
            decision   TEXT NOT NULL,  -- approve | veto | swap | correct | pick | reject
            subject    TEXT,           -- card_id / asset_id / episode / title
            chosen     TEXT,           -- what PD chose / corrected TO
            rejected   TEXT,           -- what PD rejected / corrected FROM
            reason     TEXT            -- PD's words or the inferred principle (the learning signal)
        )
        """
    )
    con.commit()


def log_selection(con: sqlite3.Connection, *, lane: str | None, kind: str,
                  decision: str, subject: str = "", chosen: str = "",
                  rejected: str = "", reason: str = "") -> None:
    """Append one PD decision. `reason` is the load-bearing field — it is what the
    selectors learn from, so state the PRINCIPLE ('산책 footage 과다 → 비-산책 신선 컨셉
    우선'), not just the instance."""
    ensure_table(con)
    con.execute(
        "INSERT INTO pd_selections (created_at, lane, kind, decision, subject, "
        "chosen, rejected, reason) VALUES (datetime('now'),?,?,?,?,?,?,?)",
        (lane or "both", kind, decision, subject, chosen, rejected, reason))
    con.commit()


def recent_selections(con: sqlite3.Connection, *, lane: str | None = None,
                      kind: str | None = None, n: int = 60) -> list[dict]:
    ensure_table(con)
    q = "SELECT * FROM pd_selections"
    where, params = [], []
    if lane:
        where.append("(lane=? OR lane='both')"); params.append(lane)
    if kind:
        where.append("kind=?"); params.append(kind)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"; params.append(n)
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(q, params).fetchall()]


def taste_digest(con: sqlite3.Connection, *, lane: str | None = None,
                 kinds: tuple[str, ...] | None = None, n: int = 60) -> str:
    """A compact 'PD가 이렇게 선택해왔다' brief for injection into a selector. Groups
    recent decisions by kind and renders decision + reason as principle bullets.
    Empty string when there's no history yet (selector falls back to its base lens)."""
    rows = recent_selections(con, lane=lane, n=n)
    if kinds:
        rows = [r for r in rows if r["kind"] in kinds]
    if not rows:
        return ""
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    label = {K_CONCEPT: "컨셉 선택", K_CLIP: "클립 선택", K_CUT: "컷 구성",
             K_CAPTION: "캡션", K_SCHEDULE: "편성", K_TONE: "톤"}
    out = ["## PD가 과거에 이렇게 선택/교정해왔다 (이 취향을 따라 골라라)"]
    for k, items in by_kind.items():
        out.append(f"\n### {label.get(k, k)}")
        seen = set()
        for r in items:
            line = (r.get("reason") or "").strip()
            if not line:
                bits = []
                if r.get("chosen"):
                    bits.append(f"채택: {r['chosen']}")
                if r.get("rejected"):
                    bits.append(f"제외: {r['rejected']}")
                line = " / ".join(bits)
            if not line or line in seen:
                continue
            seen.add(line)
            out.append(f"- [{r['decision']}] {line}")
    return "\n".join(out)


# ── Backfill: this session's (2026-06-15) corrections as the first training data ──
_SEED = [
    # (lane, kind, decision, subject, chosen, rejected, reason)
    ("real_footage", K_CLIP, REJECT, "rf_20260615_081912", "46초 풀먹방 영상",
     "낮잠 사진", "지정 컨셉(풀먹방)을 실제 영상으로 채워라 — 관련 영상 놔두고 다른 활동 사진으로 패딩하면 컨셉 드리프트. 클립 선택은 컨셉에 충실해야."),
    ("real_footage", K_CUT, REJECT, "rf_video_first", "영상 컷",
     "다초 사진 켄번즈 컷", "RF는 영상-우선. 사진은 최대 0.5초 캡션없는 플래시 액센트, 스토리/오프닝/클로저 컷 금지. 영상이 부족하면 컨셉을 바꿔라."),
    ("real_footage", K_CUT, REJECT, "rf_closer", "영상으로 마무리",
     "마지막 사진 컷", "에피소드는 실제 영상으로 끝나라 — 사진 closer 금지(0.5초 무성 플래시로 끝나면 어색)."),
    ("real_footage", K_CAPTION, CORRECT, "med_2026_05_05_grass", "할머니가 다듬는 '부추', 레오가 맛있어 보여 탐냄",
     "고양이 풀(캣그래스) 먹방", "VLM이 부추를 '고양이 풀'로 오인 → 캡션 사실 정정. 화면 사물의 실제 정체를 PD 사실로 확인."),
    ("both", K_TONE, REJECT, "caption_gimmick", "담백한 회상+위트 한 스푼, 컷마다 다른 결",
     "'인생 N년'·'N년차'·'~모드'·'베테랑 프로토콜' 반복 페르소나 라벨", "위트는 한 스푼이지 한 그릇이 아니다 — 같은 장치 2회+ 반복하면 상투어, 펫이 사람 이력서처럼 늙음."),
    ("real_footage", K_CAPTION, PICK, "memorylane_anchor", "시간 앵커는 처음·끝만 + 마지막은 첫 컷 정서 콜백(북엔드)",
     "매 컷 N년/N년차 시점 반복", "메모리레인은 처음·끝에만 시간 앵커, 가운데는 행동. payoff는 cut1 정서를 되받는 북엔드가 가장 강함."),
    ("real_footage", K_SCHEDULE, PICK, "20260616_0800", "비-산책 신선 컨셉(레오 부추)",
     "랴니 산책 (013426, 같은 날 3편째 산책)", "footage 다양성 — 같은 활동(산책) 반복 편성 금지. 비-산책 신선 소재를 슬롯에 우선."),
    ("ai_vtuber", K_CONCEPT, REJECT, "20260616_av", "신선한 미발표 컨셉",
     "분수의 여왕 / 모래섬 해적 (이미 유사물 다수)", "리뷰어 freshness가 못 걸러도 PD는 거른다 — 이미 한 컨셉/유사물은 제외. 신선도는 PD 기준."),
    ("both", K_CONCEPT, PICK, "lane_fit", "footage로 커버되는 소재(예: 눈뜨고 자는 랴니)는 RF로",
     "실사로 가능한 걸 AV로 생성", "실제 영상으로 보여줄 수 있는 소재는 RF가 낫다 — AV는 실사로 불가능한 상상/물리위반이 hook일 때."),
]


def backfill_initial(con: sqlite3.Connection) -> int:
    """Seed pd_selections with this session's corrections (idempotent — skips if the
    table already holds the seed subjects)."""
    ensure_table(con)
    existing = {r["subject"] for r in con.execute(
        "SELECT subject FROM pd_selections").fetchall()
        if (con.row_factory is sqlite3.Row)} if False else set()
    # robust existence check (row_factory may not be Row)
    cur = con.execute("SELECT subject FROM pd_selections")
    existing = {row[0] for row in cur.fetchall()}
    n = 0
    for lane, kind, decision, subject, chosen, rejected, reason in _SEED:
        if subject in existing:
            continue
        log_selection(con, lane=lane, kind=kind, decision=decision,
                      subject=subject, chosen=chosen, rejected=rejected, reason=reason)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    from agents.producer import _db
    con = _db()
    if "--backfill" in sys.argv:
        print("seeded", backfill_initial(con), "rows")
    print(taste_digest(con))
