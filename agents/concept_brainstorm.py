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


def _exclude_block(context: dict | None) -> str:
    """Sibling concepts already locked into TODAY's batch (the other slots produced
    for the same publish day). New candidates must diverge from these in SUBSTANCE —
    premise/motif/set/punchline — not merely in which clips they use. A shared broad
    format or theme (both 거실, both memory-lane) is fine; the same *story* is not.
    This is intra-batch concept-dedup, a different axis from the macro reviewer's
    footage-freshness vs PAST public uploads (which is footage-, not theme-, based)."""
    ex = (context or {}).get("exclude_concepts") or []
    lines = []
    for c in ex:
        t = (c.get("title") or c.get("theme") or "").strip()
        lg = (c.get("logline") or "").strip()
        if t or lg:
            lines.append(f"  - {t}" + (f" :: {lg}" if lg else ""))
    if not lines:
        return ""
    return ("\n★오늘 같은 배치(같은 날 공개)에 이미 확정된 회차 — 아래와 핵심 모티프·전개·"
            "배경·펀치라인이 실질적으로 겹치면 안 된다. 포맷·넓은 테마가 같은 건 괜찮지만 "
            "'같은 이야기'는 금지다. 확실히 다른 앵글/소재로 가라:\n" + "\n".join(lines) + "\n")


def _active_trends_block(style: str) -> str:
    """Timely-hook injection (PD 2026-06-24): the live `trends` rows (calendar events +
    discovered memes/challenges, fed by scripts/trend_feed.py) so brainstorm can ride
    what's hot NOW — the World Cup, Halloween, a viral pet challenge. The empty trends
    table was exactly why the AV engine never proposed timely concepts on its own. A
    recurring event lists its remaining angles + the ones used recently → rotate to a
    NEW angle, never repeat the same sub-concept (월드컵: 대결 → 응원모드 → …)."""
    import datetime as _dt
    import json as _json
    import sqlite3 as _sql
    today = _dt.date.today().isoformat()
    try:
        con = _sql.connect(str(ROOT / "data" / "agent.db"))
        rows = con.execute(
            "SELECT category, title, fit_score, notes FROM trends "
            "WHERE expiry_date IS NULL OR expiry_date >= ? "
            "ORDER BY fit_score DESC LIMIT 6", (today,)).fetchall()
        con.close()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for cat, title, fit, notes_s in rows:
        try:
            nt = _json.loads(notes_s) if notes_s else {}
        except Exception:
            nt = {}
        extra = ""
        if nt.get("angles_remaining"):
            extra += f" | 남은 각도: {', '.join(nt['angles_remaining'][:5])}"
        if nt.get("recent_used"):
            extra += f" | 이미 한 각도(반복 금지): {', '.join(nt['recent_used'][:3])}"
        if nt.get("why"):
            extra += f" | 재현: {nt['why']}"
        if nt.get("hint"):
            extra += f" | {nt['hint']}"
        lines.append(f"  · [{cat} fit{float(fit or 0):.2f}] {title}{extra}")
    return (
        "\n★오늘의 시의성 훅(지금 한국에서 핫한 소재 — 적합하면 후보 중 1~2개는 이걸 메인 훅으로 "
        "잡아라. fit≥0.9 라이브 이벤트는 강력 추천). 단 ①우리 자산/캐릭터로 자연스럽게 재현 가능할 "
        "때만 ②같은 이벤트는 '남은 각도'에서 매번 새 각도로(이미 한 각도 반복 금지) ③옷 입히기 금지 "
        "— 소품/오버레이로 테마 전달:\n" + "\n".join(lines) + "\n")


def brainstorm(style: str, brief: str, n: int = 5, *, context: dict | None = None) -> list[dict]:
    """Generate n DISTINCT storyline candidates for the brief. LLM, cheap."""
    facts = _character_facts()
    material = _real_material()
    structure = (
        "ai_vtuber는 상상을 마음껏 펼치는 레인이다. '현실→상상(과장 훅)→현실 복귀'는 강력한 "
        "기본형이지 유일한 형이 아니다 — 상상 축을 후보마다 과감히 바꿔라:\n"
        "  · 반사실/시간 믹스: '아기 랴니와 아기 레오가 만났다면?', 과거 영상 속 순간을 현재가 "
        "이어받기, 미래의 둘, 평행세계\n"
        "  · 장르 패러디: 느와르 탐정, 자연 다큐 내레이션, 무협, SF, 하이스트\n"
        "  · 사소한 일상의 과장: 벌레 한 마리→방역 작전, 택배 상자→보물, 낮잠→대서사\n"
        "  · 공간/사물 변신: 소파가 배로, 거실이 우주로 (단 매번 같은 변신 금지)\n"
        "계절은 '선택지'일 뿐 기본값이 아니다 — 이번 이야기를 더 좋게 만들 때만 쓰고, 'because "
        "여름이라 물놀이' 식 자동 연상은 피하라."
        if style == "ai_vtuber"
        else (
        "real_footage = 실제 보유 클립으로만 만드는 일상/메모리레인. 판타지·없는 사건 금지.\n"
        "★후보마다 '앵글(무엇에 관한 회차인가)'을 확실히 다르게 잡아라 — 장소만 바꾼 같은 "
        "이야기는 금지. 특히 **'랴니는 차분·레오는 호기심, 각자의 방식/각자의 자리/두 리듬/평행 "
        "동행'류의 사건 없는 공존 관찰은 이미 너무 많이 했다 — 새 후보에 다시 쓰지 마라.**\n"
        "대신 뚜렷한 축에서 골라라(매번 다른 축):\n"
        "  · 구체적 사건/활동 1개: 첫 수영·물놀이, 공놀이, 풀(캣그라스) 먹방, 눈/썰매, 간식 "
        "도둑, 목욕, 새 물건 탐험 — 클립에 그 사건이 실제로 있을 때만.\n"
        "  · 메모리레인(과거↔현재, 시점 라벨 필수): '○년 전 아기 ○○ → 지금', 같은 자리 then/now.\n"
        "  · 한 클립 원테이크: 길고 자족적인 한 순간의 arc를 통째로(트림 몽타주 말고).\n"
        "  · 관계의 한 순간: 한쪽이 다른 쪽에게 하는 구체적 상호작용(쫓기·핥기·자리 뺏기 등).\n"
        "  · 하루 흐름(시간 압축): 같은 날 오전→오후→저녁을 시점 캡션으로 교차.\n"
        "재료(episode_stories의 실제 사건 + 클립 장면)를 적극 조합해 '그 회차만의 한 줄 요약'이 "
        "서로 겹치지 않게 하라."))
    system = (
        "너는 'Ryani & Leo' 펫 숏츠의 수석 작가다. 주어진 brief로 서로 확연히 다른 "
        f"스토리라인 후보 {n}개를 브레인스토밍하라. 목표는 '시청자가 끝까지 보고 공유할' 강한 "
        "스토리. 각 후보는 훅·중간·마무리(payoff)가 분명해야 한다.\n"
        f"캐릭터(고정): {facts}\n구조: {structure}\n"
        + (f"\n★실제 재료(이걸 창의적으로 '조합·변주'해서 컨셉을 짜라 — 실제 있었던 일/실제 영상이 "
           f"가장 진짜 같고 공감된다. 무에서 지어내지 말고 아래를 비틀어라):\n{material}\n"
           if material else "")
        + "\n다양성·참신성 필수: 후보마다 다른 실제 소재 + 다른 상상 축을 써라. 실제 영상/일화 "
          "한 장면을 '상상의 도약 씨앗'으로 삼아 비틀어라(예: 아기 시절 클립 → '그때 둘이 만났다면'). "
          "최근 회차에서 이미 쓴 소재/장소/장치(물놀이·바다·분수 같은 반복 모티프)는 피하고, "
          "한 번도 안 해본 앵글을 우선하라 — 잘 만든 재탕보다 거친 신선함이 낫다.\n"
        "각 후보 = {title(한국어), logline(한 줄 요약), beats(컷별 한 줄 3~6개), "
        "imagination_hook(상상 훅 한 줄; rf면 '없음'), why_appealing(시청자가 왜 좋아할지 한 줄)}.\n"
        "후보끼리 훅이 겹치면 안 된다(다양성이 핵심). JSON 배열만 출력.")
    system += _active_trends_block(style)
    system += _exclude_block(context)
    # Channel Manager Phase 4: feed back what's WINNING (energy/format/packaging pattern,
    # not topics — freshness still rules topics). Empty until enough performance data.
    try:
        from agents.channel_manager import portfolio_signal
        system += portfolio_signal()
    except Exception:
        pass
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
        "★참신성은 핵심 가중치다: 최근 회차의 소재/장치를 재탕했거나 계절에 기댄 뻔한 후보는 "
        "아무리 매끈해도 낮게(≤5) 매기고, 새로운 상상 축·반사실·한 번도 안 해본 앵글은 높게 매겨라. "
        "신선하지 않으면 잘 만들어도 낮다.\n"
        + (("\n" + _taste + "\n위 PD 취향에 어긋나는 후보(이미 한 컨셉/유사물, 반복 소재)는 "
            "낮게, 부합하는 후보는 높게.\n") if _taste else "")
        + "각 후보에 audience_score(1-10)와 한 줄 이유(verdict_reason: 훅/몰입/마무리/공유성/참신성/"
        "PD취향 중심)를 매겨라. 가장 높은 게 렌더 대상이다. JSON 배열만: "
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


def _is_redundant_vs_batch(cand: dict, exclude: list[dict]) -> dict:
    """Same-batch concept-dedup gate: is `cand` substantially the SAME episode as a
    sibling already locked into today's batch? Substance = premise/motif/set/punchline,
    NOT a merely shared format/broad theme. Returns {redundant: bool, vs, reason}."""
    if not cand or not exclude:
        return {"redundant": False}
    system = (
        "너는 'Ryani & Leo' 숏츠 편성 PD다. 같은 날 배치에 두 회차가 '실질적으로 같은 "
        "이야기'면 시청자에겐 반복이고 한 슬롯이 낭비된다. 새 후보가 아래 기존 회차 중 "
        "하나와 핵심 모티프·전개·배경·펀치라인이 사실상 같으면 redundant=true. 단지 "
        "포맷/넓은 테마(둘 다 거실, 둘 다 메모리레인)만 같고 이야기·훅·소재가 다르면 "
        "false. JSON만: {\"redundant\":bool,\"vs\":\"겹치는 기존 제목 또는 ''\","
        "\"reason\":\"한 줄\"}.")
    user = json.dumps({
        "new": {"title": cand.get("title"), "logline": cand.get("logline"),
                "imagination_hook": cand.get("imagination_hook"),
                "beats": cand.get("beats")},
        "existing": [{"title": c.get("title") or c.get("theme"),
                      "logline": c.get("logline")} for c in exclude],
    }, ensure_ascii=False)
    txt = call_text_cascade(system, user, max_tokens=300).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt); txt = re.sub(r"\s*```$", "", txt)
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"redundant": False}
    except Exception:
        return {"redundant": False}


def best(style: str, brief: str, n: int = 5, *, context: dict | None = None) -> dict:
    """Brainstorm n, rank by audience, return {winner, ranking}.

    Same-batch concept-dedup: when context carries exclude_concepts (sibling slots
    already locked for today), walk the ranking best-first and skip any candidate that
    duplicates a sibling in substance — so two slots on one day never ship the same
    story. If ALL collide, fall back to the top-ranked (launch backstop + PD veto)."""
    cands = brainstorm(style, brief, n, context=context)
    ranked = rank_by_audience(cands, style)
    exclude = (context or {}).get("exclude_concepts") or []
    if exclude and ranked:
        winner = None
        for c in ranked:
            v = _is_redundant_vs_batch(c, exclude)
            if v.get("redundant"):
                c["_batch_redundant"] = v
                continue
            winner = c
            break
        return {"winner": winner or ranked[0], "ranking": ranked}
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
