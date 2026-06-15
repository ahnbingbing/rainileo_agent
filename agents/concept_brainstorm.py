"""Concept brainstorm + audience-gate (PD 2026-06-14).

PD's process fix: don't render one concept and review the finished $40-50 video — first
BRAINSTORM several storylines, let the reviewer judge the IDEAS from a YouTube-viewer
(audience-appeal) angle, and only render the winner. The reviewer's job moves upstream to
the cheap stage.

Flow:
  brainstorm(style, brief, n) -> [candidate, ...]          # LLM ideation, $cheap
  rank_by_audience(candidates, style) -> [scored, ...]     # reviewer §1 audience lens
  best(style, brief, n) -> {winner, ranking}               # one call site

Each candidate: {title, logline, beats[], imagination_hook, why_appealing}.
The winner's beats feed the normal render pipeline (as a /concept directive or storyboard).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from agents.llm_cascade import call_text_cascade

log = logging.getLogger("agents.concept_brainstorm")

ROOT = Path(__file__).resolve().parent.parent
_GUIDE = ROOT / "notes" / "shorts_review_agent_giri.md"


def _character_facts() -> str:
    try:
        from agents import arc
        return arc.CHARACTER_FACTS
    except Exception:
        return ("레오 = 오렌지 태비 고양이 8개월, 호기심·장난·사냥본능. "
                "랴니 = 검정 프렌치불독 11살, 꼬리 없음, 차분·의젓한 누나.")


def _real_material(limit_stories: int = 12, limit_clips: int = 12) -> str:
    """Pull REAL material from the DB so brainstorm COMBINES actual events/footage (PD: "VLM에
    있는 내용들을 조합해서 더 창의적으로"). Real anecdotes (episode_stories) + vivid VLM scene
    descriptions = authentic, surprising concept seeds — not generic invention."""
    import sqlite3
    bits: list[str] = []
    try:
        con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
        try:
            rows = con.execute(
                "SELECT text FROM episode_stories WHERE author NOT LIKE '%참여%' "
                "AND length(text) > 25 ORDER BY rowid DESC LIMIT ?", (limit_stories,)).fetchall()
            real = [r[0].strip().replace("\n", " ")[:140] for r in rows if r[0]]
            if real:
                bits.append("실제 있었던 일(보호자 기록):\n- " + "\n- ".join(real))
        except Exception:
            pass
        try:
            # vivid, distinct real clips across years (round-robin-ish by recency)
            rows = con.execute(
                "SELECT DISTINCT substr(scene_description,1,120) FROM assets "
                "WHERE kind='video' AND scene_description IS NOT NULL AND scene_description!='' "
                "AND length(scene_description) > 40 ORDER BY RANDOM() LIMIT ?", (limit_clips,)).fetchall()
            clips = [r[0].strip().replace("\n", " ") for r in rows if r[0]]
            if clips:
                bits.append("실제 보유 영상 장면(조합 재료):\n- " + "\n- ".join(clips))
        except Exception:
            pass
        con.close()
    except Exception:
        pass
    return "\n\n".join(bits)


def _audience_rubric() -> str:
    """The §1 audience-appeal mission from the review guide, so ranking uses the same lens."""
    try:
        txt = _GUIDE.read_text(encoding="utf-8")
        m = re.search(r"## 1\. Core mission(.+?)## 2\.", txt, re.S)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ("Judge as a scrolling YouTube viewer: hook in 1-2s, watch-through (no dead "
            "middle), a payoff/button, charm & relatability, rewatch/share-worthy.")


def brainstorm(style: str, brief: str, n: int = 5, *, context: dict | None = None) -> list[dict]:
    """Generate n DISTINCT storyline candidates for the brief. LLM, cheap."""
    facts = _character_facts()
    material = _real_material()
    structure = ("ai_vtuber면 보통 현실→상상(과장 훅)→현실 복귀 구조가 강하다(상상 훅은 후보마다 "
                 "완전히 다르게 — 예: 소파가 배로 변신, 거실이 우주로, 바닥이 바다로, 차가 우주선/배로). "
                 if style == "ai_vtuber"
                 else "real_footage면 실제 클립으로 가능한 일상/메모리레인 스토리. 판타지 금지.")
    system = (
        "너는 'Ryani & Leo' 펫 숏츠의 수석 작가다. 주어진 brief로 서로 확연히 다른 "
        f"스토리라인 후보 {n}개를 브레인스토밍하라. 목표는 '시청자가 끝까지 보고 공유할' 강한 "
        "스토리. 각 후보는 훅·중간·마무리(payoff)가 분명해야 한다.\n"
        f"캐릭터(고정): {facts}\n구조: {structure}\n"
        + (f"\n★실제 재료(이걸 창의적으로 '조합·변주'해서 컨셉을 짜라 — 실제 있었던 일/실제 영상이 "
           f"가장 진짜 같고 공감된다. 무에서 지어내지 말고 아래를 비틀어라):\n{material}\n"
           if material else "")
        + "\n다양성 필수: 후보마다 다른 실제 소재 + 다른 훅을 써라(자동차 씬, 카페, 사냥본능, "
          "메모리레인 등 폭넓게).\n"
        "각 후보 = {title(한국어), logline(한 줄 요약), beats(컷별 한 줄 3~6개), "
        "imagination_hook(상상 훅 한 줄; rf면 '없음'), why_appealing(시청자가 왜 좋아할지 한 줄)}.\n"
        "후보끼리 훅이 겹치면 안 된다(다양성이 핵심). JSON 배열만 출력.")
    user = f"brief: {brief}\n스타일: {style}\n후보 {n}개를 JSON 배열로."
    txt = call_text_cascade(system, user, max_tokens=2500).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt)
    try:
        out = json.loads(txt)
        return out if isinstance(out, list) else out.get("candidates", [])
    except Exception:
        return []


def rank_by_audience(candidates: list[dict], style: str) -> list[dict]:
    """Score each candidate by the reviewer's YouTube-audience lens. Returns the list with
    added {audience_score 1-10, verdict_reason}, sorted best-first."""
    if not candidates:
        return []
    # PD 2026-06-15: the reviewer SELECTS by PD's accumulated taste, not a generic
    # audience lens alone (PD: "기존 내 선택들을 학습해서"). Inject the PD-taste digest so
    # repeat/stale concepts PD has rejected score low and PD-preferred angles score high.
    _taste = ""
    try:
        from agents import pd_taste as _pt
        from agents.producer import _db as _getdb
        _taste = _pt.taste_digest(_getdb(), lane=style,
                                  kinds=(_pt.K_CONCEPT, _pt.K_SCHEDULE, _pt.K_TONE))
    except Exception as e:
        log.warning("pd_taste digest unavailable: %s", e)
    system = (
        "너는 'Ryani & Leo' 숏츠 리뷰어다. 아래 스토리라인 후보들을 **유튜브 시청자 관점 + PD 취향**으로 "
        "평가하라(완성도/제작난이도 아님). 기준:\n" + _audience_rubric() + "\n"
        + (("\n" + _taste + "\n위 PD 취향에 어긋나는 후보(이미 한 컨셉/유사물, 반복 소재)는 "
            "낮게, 부합하는 후보는 높게.\n") if _taste else "")
        + "각 후보에 audience_score(1-10)와 한 줄 이유(verdict_reason: 훅/몰입/마무리/공유성/PD취향 "
        "중심)를 매겨라. 가장 높은 게 렌더 대상이다. JSON 배열만: "
        "[{\"title\":..,\"audience_score\":n,\"verdict_reason\":..}] (입력 순서 유지).")
    user = json.dumps([{"title": c.get("title"), "logline": c.get("logline"),
                        "imagination_hook": c.get("imagination_hook"),
                        "why_appealing": c.get("why_appealing")} for c in candidates],
                      ensure_ascii=False)
    txt = call_text_cascade(system, user, max_tokens=1500).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt); txt = re.sub(r"\s*```$", "", txt)
    try:
        scores = json.loads(txt)
    except Exception:
        scores = []
    by_title = {s.get("title"): s for s in scores if isinstance(s, dict)}
    for i, c in enumerate(candidates):
        s = by_title.get(c.get("title")) or (scores[i] if i < len(scores) and isinstance(scores[i], dict) else {})
        c["audience_score"] = float(s.get("audience_score", 0) or 0)
        c["verdict_reason"] = s.get("verdict_reason", "")
    return sorted(candidates, key=lambda c: -c.get("audience_score", 0))


def best(style: str, brief: str, n: int = 5, *, context: dict | None = None) -> dict:
    """Brainstorm n, rank by audience, return {winner, ranking}."""
    cands = brainstorm(style, brief, n, context=context)
    ranked = rank_by_audience(cands, style)
    return {"winner": ranked[0] if ranked else None, "ranking": ranked}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="ai_vtuber", choices=["ai_vtuber", "real_footage"])
    ap.add_argument("--brief", required=True)
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    r = best(a.style, a.brief, a.n)
    for i, c in enumerate(r["ranking"], 1):
        print(f"\n[{i}] {c.get('audience_score')}/10  {c.get('title')}")
        print(f"    훅: {c.get('imagination_hook')}")
        print(f"    logline: {c.get('logline')}")
        print(f"    왜: {c.get('verdict_reason') or c.get('why_appealing')}")
    w = r["winner"]
    print("\n=== WINNER ===", (w or {}).get("title"), (w or {}).get("audience_score"))
