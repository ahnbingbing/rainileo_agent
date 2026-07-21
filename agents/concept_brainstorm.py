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
        # PD 2026-06-30: use grandmompapa content MORE BROADLY (RF AND AV). The owner's
        # EXPLICIT episode ideas/requests and the descriptions they post WITH each clip are
        # the truest seeds — surface them FIRST, above generic VLM scenes.
        try:  # 1) owner's explicit [컨셉]/[요청] — highest-priority narrative seeds
            rows = con.execute(
                "SELECT text FROM episode_stories WHERE (text LIKE '[컨셉]%' OR text LIKE '[요청]%') "
                "ORDER BY rowid DESC LIMIT 10").fetchall()
            want = [r[0].strip().replace("\n", " ")[:140] for r in rows if r[0]]
            if want:
                bits.append("★보호자가 직접 원한 컨셉/요청 (최우선 반영 — 후보 중 최소 1개는 "
                            "이걸로 짜라):\n- " + "\n- ".join(want))
        except Exception:
            pass
        try:  # 2) recent owner clip descriptions (what the NEW grandmompapa footage actually is)
            rows = con.execute(
                "SELECT DISTINCT pd_notes FROM assets WHERE pd_notes IS NOT NULL "
                "AND length(pd_notes) > 12 AND pd_notes NOT LIKE '[BRANDING]%' "
                "ORDER BY ingested_iso DESC LIMIT 12").fetchall()
            owner = [r[0].strip().replace("\n", " ")[:140] for r in rows if r[0]]
            if owner:
                bits.append("최근 보호자가 직접 설명한 실제 클립(grandmompapa — 이 일들을 컨셉으로):\n- "
                            + "\n- ".join(owner))
        except Exception:
            pass
        try:  # 3) CURATED discrete 소재 (scripts/_curate_episode_material.py distills the raw
              # grandma-conversation dump into deduped trait/event/preference items). Prefer it —
              # the raw episode_stories dump was conversational noise; fall back to raw if empty.
            rows = con.execute(
                "SELECT kind, subjects, material FROM episode_material "
                "ORDER BY use_count ASC, RANDOM() LIMIT ?", (limit_stories,)).fetchall()
            real = [f"[{r[0]}/{r[1]}] {r[2].strip()}"[:150] for r in rows if r[2]]
            if not real:  # fallback: raw dump (pre-curation)
                rows = con.execute(
                    "SELECT text FROM episode_stories WHERE author NOT LIKE '%참여%' "
                    "AND text NOT LIKE '[%' AND length(text) > 25 ORDER BY rowid DESC LIMIT ?",
                    (limit_stories,)).fetchall()
                real = [r[0].strip().replace("\n", " ")[:140] for r in rows if r[0]]
            if real:
                bits.append("실제 있었던 일 — 소재(보호자 기록에서 추린 것):\n- " + "\n- ".join(real))
        except Exception:
            pass
        try:
            # PD 2026-07-21: RECENT-first, not flat RANDOM. A random sample over 3000+ all-time
            # clips drowned the ~180 fresh grandmompapa July clips (≈6%) → concepts drifted to old
            # off-season footage (Christmas in July) while the newly-arrived summer footage sat
            # unused. Bias the material toward what JUST came in (majority recent by capture date)
            # + a random tail so memory-lane variety survives. The comment always said "by recency";
            # the query was RANDOM — this makes it true.
            _base = ("SELECT DISTINCT substr(scene_description,1,120) FROM assets "
                     "WHERE kind='video' AND scene_description IS NOT NULL AND scene_description!='' "
                     "AND length(scene_description) > 40 ")
            n_recent = max(1, int(limit_clips * 0.6))
            recent = con.execute(_base + "ORDER BY captured_iso DESC LIMIT ?", (n_recent,)).fetchall()
            rnd = con.execute(_base + "ORDER BY RANDOM() LIMIT ?", (limit_clips,)).fetchall()
            seen, clips = set(), []
            for r in list(recent) + list(rnd):     # recent first, dedup, random fills the rest
                v = (r[0] or "").strip().replace("\n", " ")
                if v and v not in seen:
                    seen.add(v)
                    clips.append(v)
                if len(clips) >= limit_clips:
                    break
            if clips:
                bits.append("실제 보유 영상 장면 (최근 촬영 우선 — 새로 들어온 grandmompapa 소재 포함, 조합 재료):\n- "
                            + "\n- ".join(clips))
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


def _overused_format_block(limit: int = 30, min_count: int = 3) -> str:
    """The batch dedup catches repeated TOPICS but not repeated FORMATS/frames — so '댕냥 챌린지'
    and the '나 20XX년생인데' frame shipped ~10× in three weeks while each looked 'new' by topic.
    Scan recent card themes for a small set of format markers and, when one recurs a lot, tell the
    brainstorm to ROTATE the format (not just the topic) — and if it must ride a tired format,
    make it genuinely different (fresh stage/outdoors, not the same indoor comparison). Empty when
    nothing over-repeats (so it never over-constrains a healthy pool)."""
    try:
        import sqlite3
        from collections import Counter
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT theme FROM cards WHERE theme IS NOT NULL "
                           "ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
        con.close()
        markers = ["챌린지", "년생", "뱃살", "POV", "관찰기"]
        c = Counter()
        for (th,) in rows:
            for m in markers:
                if m in (th or ""):
                    c[m] += 1
        hot = [f"{k}({v}회)" for k, v in c.most_common() if v >= min_count]
        if not hot:
            return ""
        return ("\n★최근 과다 반복된 포맷/프레임(이번엔 피하라): " + ", ".join(hot) + ". 같은 틀"
                "('OO 챌린지', '나 20XX년생인데~' 비교물)을 또 쓰지 말고 완전히 다른 형식으로. 굳이 "
                "챌린지류를 간다면 실내 비교가 아니라 야외·독특한 각도 등 확실히 새롭게.\n")
    except Exception:
        return ""


def _active_trend_rows(limit: int = 6) -> list[tuple]:
    """Live `trends` rows (calendar events + discovered memes/challenges, fed by
    scripts/trend_feed.py), highest-fit first. Shared by the prompt block and the
    has_active_trends() gate so 'is there anything timely right now?' has one answer."""
    import datetime as _dt
    import sqlite3 as _sql
    today = _dt.date.today().isoformat()
    try:
        con = _sql.connect(str(ROOT / "data" / "agent.db"))
        rows = con.execute(
            "SELECT category, title, fit_score, notes FROM trends "
            "WHERE expiry_date IS NULL OR expiry_date >= ? "
            "ORDER BY fit_score DESC LIMIT ?", (today, limit)).fetchall()
        con.close()
        return rows
    except Exception:
        return []


def has_active_trends() -> bool:
    """True when at least one timely hook is live. Lets a caller that REQUIRES a timely
    episode (the daily 'one of two AVs must be 시의성' slot) check honestly whether
    enforcement is even possible — there's nothing to ride if the trends table is dry."""
    return bool(_active_trend_rows(limit=1))


def _active_trends_block(style: str, *, require: bool = False) -> str:
    """Timely-hook injection (PD 2026-06-24): so brainstorm can ride what's hot NOW —
    the World Cup, Halloween, a viral pet challenge. The empty trends table was exactly
    why the AV engine never proposed timely concepts on its own. A recurring event lists
    its remaining angles + the ones used recently → rotate to a NEW angle, never repeat
    the same sub-concept (월드컵: 대결 → 응원모드 → …).

    `require=True` (PD 2026-06-25): this slot is the day's designated 시의성 AV — exactly
    one of the two daily AVs must ride a timely hook, guaranteed, not merely suggested.
    Then EVERY candidate must be built on one of today's hooks (mandatory), so whichever
    one the ranker picks is timely. require is meaningful only for ai_vtuber: RF rides a
    hook solely when real footage already supports it, so it never forces."""
    rows = _active_trend_rows(limit=6)
    if not rows:
        return ""
    import json as _json
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
    if require and style == "ai_vtuber":
        return (
            "\n★★이 회차는 '오늘의 시의성 AV'다 — 반드시 아래 시의성 훅 중 하나를 메인 훅으로 잡아라"
            "(선택 아님, 강제). 후보 5개를 서로 다른 훅/각도로 내되 전부 시의성 소재 위에 세워라. "
            "가장 라이브하고 fit 높은 것(월드컵 등)을 우선 고려하라. 제약은 동일: ①우리 자산/캐릭터로 "
            "자연스럽게 재현 가능한 각도로 ②같은 이벤트는 '남은 각도'에서 새 각도로(반복 금지) ③옷 "
            "입히기 금지 — 소품/오버레이로 테마 전달:\n" + "\n".join(lines) + "\n")
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
        "★★무대(장소)를 다양하게 — 집 거실에만 갇히지 마라 (PD 2026-07-16): AV는 생성이라 "
        "**어디든 갈 수 있다**(RF는 실제 찍은 곳만 가능). 매 회차 거실이면 단조로워 클릭이 안 난다. "
        "후보마다 무대를 과감히 바꿔라 — **해변·바다·공원·숲·계곡·물가·거리·카페·눈밭·옥상** 같은 "
        "야외/외출 무대를 적극 제안하고(집 안이면 방을 바꿔서라도), 그 무대를 set_anchor로 명시하라. "
        "(canon 활용: 랴니=물 마니아라 해변·물놀이·분수에 딱, 레오=물 조심조심; 눈·썰매는 랴니가 주인공.) "
        "적어도 후보 절반은 거실 밖 장소로 잡아라.\n"
        "계절은 '선택지'일 뿐 기본값이 아니다 — 이번 이야기를 더 좋게 만들 때만 쓰고, 'because "
        "여름이라 물놀이' 식 자동 연상은 피하라.\n"
        "★★AV는 자기 레인을 정당화해야 한다 (PD 2026-07-01, 절대): ai_vtuber는 비싼 AI 생성이라 "
        "RF(실사)가 못 하는 걸 할 때만 존재 이유가 있다. **모든 후보는 RF로는 못 만들 강한 훅을 "
        "반드시 가져라** — 상상/판타지, 물리적으로 불가능한 갸그, 강한 named 컨셉/포맷, 또렷한 "
        "반전·payoff 중 적어도 하나. 그냥 일상 펫 행동(창밖 보기·방 안 어슬렁·나란히 누움·서로 "
        "쳐다봄·장난감 깨작)으로 끝나는 저자극 컨셉은 **AV 후보로 금지**(그건 RF 거다). "
        "imagination_hook이 '없음'이거나 약하면 그 후보를 버리고 더 센 훅으로 다시 짜라. "
        "또 가능하면 시의성(지금 핫한 밈·절기·이벤트)을 얹어라 — PD: '시의성 있고 hook 있는 게 "
        "들어가야 한다.' 'why_appealing'에 '왜 RF 아닌 AV인지 + 끝까지 볼 훅'이 한 줄로 서야 한다."
        if style == "ai_vtuber"
        else (
        "real_footage = 실제 보유 클립으로만 만드는 일상/메모리레인. 판타지·없는 사건 금지.\n"
        "★후보마다 '앵글(무엇에 관한 회차인가)'을 확실히 다르게 잡아라 — 장소만 바꾼 같은 "
        "이야기는 금지. 특히 **'랴니는 차분·레오는 호기심, 각자의 방식/각자의 자리/두 리듬/평행 "
        "동행'류의 사건 없는 공존 관찰은 이미 너무 많이 했다 — 새 후보에 다시 쓰지 마라.**\n"
        "대신 뚜렷한 축에서 골라라(매번 다른 축):\n"
        "  · 구체적 사건/활동 1개: 물놀이/수영(랴니=베테랑 물개라 '첫 수영' 아님), 공놀이, "
        "풀(캣그라스) 먹방, 눈/썰매, 간식 도둑, 목욕, 새 물건 탐험 — 클립에 그 사건이 실제로 있을 때만.\n"
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
           "★위 '보호자가 직접 원한 컨셉/요청'과 '보호자가 설명한 실제 클립(grandmompapa)'은 "
           "소유자의 실제 의도/사실이다 — 후보 중 **최소 1개는 반드시 그 내용으로** 짜고, 그 사실이 "
           "말하지 않은 설정(시간대·사건·드라마)을 덧붙여 날조하지 마라. (PD: grandmompapa 내용을 "
           "RF·AV 양쪽에 폭넓게 써라.)\n"
           if material else "")
        + "\n다양성·참신성 필수: 후보마다 다른 실제 소재 + 다른 상상 축을 써라. 실제 영상/일화 "
          "한 장면을 '상상의 도약 씨앗'으로 삼아 비틀어라(예: 아기 시절 클립 → '그때 둘이 만났다면'). "
          "최근 회차에서 이미 쓴 소재/장소/장치(물놀이·바다·분수 같은 반복 모티프)는 피하고, "
          "한 번도 안 해본 앵글을 우선하라 — 잘 만든 재탕보다 거친 신선함이 낫다.\n"
        "각 후보 = {title(한국어), logline(한 줄 요약), beats(컷별 한 줄 3~6개), "
        "imagination_hook(상상 훅 한 줄; rf면 '없음'), why_appealing(시청자가 왜 좋아할지 한 줄)}.\n"
        "후보끼리 훅이 겹치면 안 된다(다양성이 핵심). JSON 배열만 출력.")
    system += _active_trends_block(
        style, require=bool((context or {}).get("require_timely")))
    system += _exclude_block(context)
    system += _overused_format_block()
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


# Generic, non-distinctive words — shared between two concepts they say NOTHING about
# whether the STORY is the same (both mention 랴니/여름/하루). Stripped before comparing.
_DEDUP_STOP = {
    "랴니", "레오", "ryani", "leo", "우리", "그리고", "함께", "같이", "나란히", "서로",
    "오늘", "하루", "일상", "순간", "시간", "이야기", "그때", "지금", "매일", "다시",
    "여름", "봄", "가을", "겨울", "아침", "저녁", "오후", "낮", "밤", "계절",
    "친구", "남매", "누나", "동생", "막내", "아기", "우리집", "집사", "반려동물",
    "그", "이", "저", "것", "너와", "그리고", "shorts", "숏츠", "vlog", "브이로그",
}
# Surface variants that mean the same thing — normalized so "딱딱한" collides with "단단한".
_DEDUP_SYN = {"딱딱": "단단", "딱딱한": "단단", "단단한": "단단",
             "강아지간식": "간식", "먹방": "간식", "간식들": "간식"}
# Common Korean trailing particles — stripped so 간식/간식을/간식이 count as one token.
_DEDUP_PARTICLES = ("에서", "으로", "에게", "까지", "부터", "처럼", "이랑", "하고",
                    "을", "를", "이", "가", "은", "는", "의", "에", "도", "만", "과", "와", "랑")


def _dedup_tokens(*texts: str) -> set[str]:
    """Distinctive content tokens of a concept's text (title/theme + logline). Hangul
    words ≥2 chars (particles stripped) + English words ≥3 chars, minus the generic
    stoplist. Two concepts sharing several of these describe the SAME episode."""
    toks: set[str] = set()
    for text in texts:
        for raw in re.findall(r"[가-힣]{2,}|[a-z0-9]{3,}", (text or "").lower()):
            t = raw
            if re.fullmatch(r"[가-힣]+", t):
                for p in _DEDUP_PARTICLES:
                    if t.endswith(p) and len(t) - len(p) >= 2:
                        t = t[: -len(p)]; break
            t = _DEDUP_SYN.get(t, t)
            if t and t not in _DEDUP_STOP:
                toks.add(t)
    return toks


def _concept_lexical_collision(cand: dict, exclude: list[dict], *, min_shared: int = 2) -> dict:
    """DETERMINISTIC concept-dedup — the guarantee the LLM 'diverge from these' prompt
    is not. `exclude` = sibling slots + recently-published episodes (last ~14d). If the
    candidate shares ≥`min_shared` distinctive content tokens with ANY of them, it is a
    re-tread (e.g. '단단한 간식 앞에서 랴니의 분투' vs '6년 전 랴니의 분투기 — 단단한 간식').
    Returns {collision, vs, shared}. Cheap; no LLM. Feed its verdict to the caller —
    do NOT rely on the model to notice it re-treaded."""
    if not cand or not exclude:
        return {"collision": False}
    ct = _dedup_tokens(cand.get("title") or cand.get("theme") or "", cand.get("logline") or "")
    if not ct:
        return {"collision": False}
    for c in exclude:
        et = _dedup_tokens(c.get("title") or c.get("theme") or "", c.get("logline") or "")
        shared = ct & et
        if len(shared) >= min_shared:
            return {"collision": True,
                    "vs": (c.get("title") or c.get("theme") or "").strip(),
                    "shared": sorted(shared)}
    return {"collision": False}


# Format/packaging words describe HOW a concept is presented, not WHAT the gag is — they
# recur across unrelated concepts (a 페스티벌·챌린지·배틀 is not a repeated gag), so they must
# never count as the distinctive repeated element.
_CONCEPT_FORMAT_WORDS = {
    "페스티벌", "챌린지", "배틀", "대잔치", "대작전", "작전", "대결", "유니버스", "미션",
    "루틴", "리액션", "타임", "모먼트", "스페셜", "에디션", "파티", "월드", "버전", "브이로그",
}


def _concept_gag_collision(cand: dict, exclude: list[dict], *, df_max: int | None = None) -> dict:
    """Catch a repeated SPECIFIC concept (same distinctive gag noun — 워터밤·수박·삼계탕) while
    ALLOWING seasonal THEME revisits (여름·에어컨 recurring is fine — PD 2026-07-14). The signal
    is RARITY across channel history: a token that appears in only a FEW past concepts is that
    concept's distinctive gag; a token common across history is a theme. So flag a candidate that
    reuses a RARE past gag token — substring-tolerant, since the lexical gate missed '워터밤'
    hiding inside '대워터밤'. `df_max` = max past-concept count for a token to still count as a
    distinctive gag (default 2 → 에어컨/뱃살 that recur often are treated as themes, not gags)."""
    if not cand or not exclude:
        return {"collision": False}
    if df_max is None:
        df_max = int(os.getenv("CONCEPT_GAG_DF_MAX", "2"))
    from collections import Counter
    hist = [((h.get("title") or h.get("theme") or ""),
             _dedup_tokens(h.get("title") or h.get("theme") or "", h.get("logline") or ""))
            for h in exclude]
    df: Counter = Counter()
    for _t, ts in hist:
        for tok in ts:
            df[tok] += 1
    ct = _dedup_tokens(cand.get("title") or cand.get("theme") or "", cand.get("logline") or "")
    ct = {c for c in ct if len(c) >= 3 and c not in _CONCEPT_FORMAT_WORDS}
    for title, ht in hist:
        for t in ht:
            if len(t) < 3 or t in _CONCEPT_FORMAT_WORDS or df[t] > df_max:
                continue  # short / format / common-theme token → not a distinctive gag
            for c in ct:
                if t == c or t in c or c in t:  # substring-tolerant: 대워터밤 ⊃ 워터밤
                    return {"collision": True, "vs": title.strip(), "shared": t}
    return {"collision": False}


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
    # PD 2026-06-25: when this slot is the day's REQUIRED 시의성 AV but the trends table
    # is dry (nothing live to ride), enforcement is impossible — degrade to a normal
    # proposal and say so, rather than silently pretending it's timely.
    if (context or {}).get("require_timely") and not has_active_trends():
        log.warning("require_timely set but no active trends — proposing non-timely "
                    "(trend_feed found nothing live for today)")
    cands = brainstorm(style, brief, n, context=context)
    ranked = rank_by_audience(cands, style)
    exclude = (context or {}).get("exclude_concepts") or []
    if exclude and ranked:
        winner = None
        for c in ranked:
            # Distinctive-gag repeat first — a rare past gag noun reused (워터밤 twice) is the
            # re-tread PD keeps catching; substring-tolerant so it survives rewording, and
            # rarity-weighted so seasonal themes (여름/에어컨) are NOT treated as repeats.
            gc = _concept_gag_collision(c, exclude)
            if gc.get("collision"):
                c["_batch_redundant"] = {"redundant": True, "vs": gc.get("vs"),
                                         "reason": f"이미 만든 특정 컨셉 재탕 — '{gc.get('shared')}'"}
                continue
            # Deterministic first — a shared-core-noun collision is a re-tread the LLM
            # 'diverge' check keeps missing; don't rely on the model to notice it.
            lc = _concept_lexical_collision(c, exclude)
            if lc.get("collision"):
                c["_batch_redundant"] = {"redundant": True, "vs": lc.get("vs"),
                                         "reason": f"핵심 소재 겹침: {', '.join(lc.get('shared', []))}"}
                continue
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
