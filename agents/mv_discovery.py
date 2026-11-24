#!/usr/bin/env python3
"""Stage 0 — MV concept DISCOVERY.

The heart of the MV pipeline (PD 2026-11-19): the agent must PROPOSE the concept
itself by reading the real material — 함미하비's story (pd_notes), the character
canon, and the VLM footage library — and PD selects. Not a stub, not PD-handed by
default: "컨셉을 고르지 말고, 네가 RAG+함미하비 스토리 보고 잡는 게 주요한 거야."

This REUSES the daily brainstorm's material loaders verbatim (agents.concept_brainstorm
._character_facts / _real_material) so MV concepts are grounded in the same real
pd_notes + episode_material + VLM scene descriptions the episode engine uses — not a
parallel data path. Only the ideation PROMPT is MV-specific (original song + real-story
lyrics, genre lane, onomatopoeia for the Director to animate, a killpoint, ~60-80s arc).

Output = concept candidates for PD to pick from. The chosen one seeds the song stage.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from agents.llm_cascade import call_text_cascade
from agents.concept_brainstorm import _character_facts, _real_material
from agents.mv_principles import MV_PRINCIPLES

log = logging.getLogger("agents.mv_discovery")


def _season_hint(now: datetime | None = None) -> str:
    """Current-moment seasonal cues so concepts are TIMELY to when the MV airs.
    A hardcoded holiday ('할로윈') goes stale the day it passes and defeats the whole
    point of 'timely'; derive the cue from today's date instead. Cues lean on
    documented character facts (랴니=물 마니아·눈/얼음썰매 러버; 레오=물 회피) so the
    seasonal angle is also on-character."""
    now = now or datetime.now()
    m, d = now.month, now.day
    if m == 12 or m <= 2:
        return ("한겨울 — 첫눈·눈놀이(★랴니는 눈/얼음썰매 러버라 눈밭 on-character, 레오는 추위 피해 실내), "
                "크리스마스·연말·새해, 바깥 추위↔집 안 온기 대비")
    if 3 <= m <= 5:
        return "봄 — 벚꽃·새싹, 첫 봄나들이 산책, 따뜻해진 햇볕에 늘어진 낮잠, 창가 봄바람"
    if 6 <= m <= 8:
        return ("여름 — 무더위·장마, 물놀이(★랴니=물 마니아 수영 고수, 레오=물가 구경꾼), "
                "선풍기·에어컨 앞 뒹굴, 계곡·바다")
    # 9–11: autumn, but late Nov already tips into early winter / holiday ramp
    if m == 11 and d >= 20:
        return ("늦가을→초겨울 전환 — 첫눈 임박(★랴니 눈 러버), 연말·크리스마스 시즌 진입, "
                "쌀쌀해진 날 집 안 온기 찾기, 낙엽 산책의 끝물")
    return "가을 — 단풍·낙엽 산책, 선선한 바람, 창가 햇살 낮잠, 수확·먹을거리 풍성"


# MV theme priorities + the RF/AV routing axis (PD 2026-11-19: content-origin, NOT song genre,
# decides RF-vs-AV — and the decision is made HERE at stage 0 so it propagates to Director→board).
def _mv_direction(now: datetime | None = None) -> str:
    return (
        "주제는 함미하비 실제 스토리(pd_notes)와 캐릭터 지식에서 발굴한다 — 실제 사건 = 저작권 0 + 진정성.\n"
        "★두 축을 반드시 만족시켜라:\n"
        "  (1) **신선함** — 이미 만든 MV와 핵심 모티프가 겹치면 안 된다(아래 제외 목록 참고). "
        "매번 '두 식구 집 안 일상 케미'로 수렴하는 게 가장 흔한 실패다. 함미하비 소재에서 "
        "아직 노래로 만들지 않은 **구체적 사건/장소/계절 순간**을 파고들어라.\n"
        "  (2) **타임리(계절성)** — 후보 중 최소 1~2개는 지금 이 시점에 어울리는 절기/계절 컨셉이어야 한다. "
        f"지금의 계절 신호: {_season_hint(now)}. (단 같은 계절이라도 이미 한 앵글은 피하라 — "
        "예: 겨울 '실내 온돌 모임'을 했으면 겨울이라도 눈밭·크리스마스 같은 다른 앵글로.)\n"
        "\n★★RF/AV 비중은 **곡 장르가 아니라 콘텐츠 출처**가 정한다(PD 하드룰). 후보마다 `origin`을 판단하라:\n"
        "  · **real_story** = 우리가 실제로 겪은 일이고 그 순간의 **실사 footage가 라이브러리에 있다**(예: 랴니 "
        "개구리자세·레오 뒹굴은 실제 영상 존재). → **rf_weight='rf_heavy'**: 실제 클립이 뼈대, AV는 못 채우는 "
        "컨셉/무대/타이틀 컷만. 산책냥 MV처럼 실사 섞는다.\n"
        "  · **concept_shot** = 지어낸 컨셉·스펙터클·물리적으로 불가능한 갸그(실사로 못 찍음). → "
        "**rf_weight='av_heavy'**: AV 생성이 뼈대, 실사는 양념.\n"
        "  · 둘 다면 balanced. **real_story는 반드시 referenced_material에 실제 클립/사건을 근거로 대라**(없는 걸 "
        "real_story라 하지 마라). 음악 장르(발라드/댄스)는 보컬·편곡에만 영향, RF/AV 비중과 무관.\n"
    )


def _mv_exclude_block(context: dict | None) -> str:
    """Past MV themes already made — propose something substantially different."""
    ex = (context or {}).get("exclude_themes") or []
    ex = [str(t).strip() for t in ex if str(t).strip()]
    if not ex:
        return ""
    return ("\n★이미 만든 MV 주제(핵심 모티프가 겹치면 안 됨 — 확실히 다른 앵글로):\n- "
            + "\n- ".join(ex) + "\n")


def propose_mv_concepts(n: int = 4, *, theme: str | None = None,
                        context: dict | None = None) -> list[dict]:
    """Propose n distinct MV concept candidates grounded in real 함미하비 material.
    If `theme` is given (PD occasionally hands one), at least one candidate must build
    on it; the rest still explore. Returns [] on LLM/parse failure (caller handles)."""
    facts = _character_facts()
    material = _real_material()          # 함미하비 pd_notes + curated 소재 + VLM scenes

    system = (
        "너는 'Ryani & Leo' 채널의 오리지널 MV 기획자다. 우리 곡을 직접 만들고(저작권 0), 가사는 "
        "랴니·레오의 실제 이야기로 쓴다. 네 임무 = 함미하비의 실제 스토리와 영상 소재에서 "
        f"서로 확연히 다른 MV 컨셉 후보 {n}개를 **발굴**하는 것(무에서 짓지 말고 실제 소재를 비틀어라).\n"
        f"캐릭터(고정, 어기면 치명): {facts}\n"
        "랴니=물 마니아·무꼬리·의젓한 누나, 레오=2025-11-15 구조된 수컷 아기 고양이·장난꾸러기. "
        "종 혼동(개↔고양이)·무꼬리·물공포(랴니는 물매니아, 물공포 거짓) 금지.\n\n"
        + _mv_direction()
        + "\n" + MV_PRINCIPLES
        + (f"\n★실제 재료(이걸 '조합·변주'해 컨셉을 짜라 — 함미하비가 직접 설명한 실제 클립/사건이 "
           f"가장 진짜 같고 공감된다. 아래에 없는 사건·주체·감정을 날조하지 마라):\n{material}\n"
           if material else "")
        + _mv_exclude_block(context)
        + (f"\n★PD가 이번에 준 주제: '{theme}' — 후보 중 최소 1개는 이걸로 짜라.\n" if theme else "")
        + "\n각 후보 = {\n"
        "  theme(한 줄 주제),\n"
        "  title(한국어 가제),\n"
        "  logline(한 줄 요약 — 무슨 이야기를 노래하나),\n"
        "  origin('real_story' 또는 'concept_shot' — 위 RF/AV 하드룰대로),\n"
        "  rf_weight('rf_heavy'|'av_heavy'|'balanced' — origin에서 도출),\n"
        "  music_genre(발라드/댄스/힙합 등 — 보컬·편곡용, RF/AV와 무관),\n"
        "  vocal_mood(cute_male|mature_male|cute_female|sultry_female 중 — 화자 캐릭터×무드로),\n"
        "  story_beats(가사/영상이 될 한 줄 비트 3~6개),\n"
        "  onomatopoeia_hooks(가사에 살릴 의성어·의태어 후보 몇 개 — '톡톡/폴짝/골골골' 류, Director가 동작으로 살림),\n"
        "  killpoint(감정 정점 한 장면 — 오마쥬·클로즈업의 중심이 될 비트),\n"
        "  referenced_material(이 컨셉이 나온 실제 함미하비 노트/사건 — 어디서 발굴했는지),\n"
        "  why_mv(왜 이게 MV로 좋은지·끝까지 볼 훅 한 줄)\n"
        "}.\n"
        "후보끼리 주제·훅이 겹치면 안 된다(다양성이 핵심). JSON 배열만 출력.")
    user = f"MV 컨셉 후보 {n}개를 JSON 배열로. 실제 함미하비 소재에 그라운딩."

    txt = call_text_cascade(system, user, max_tokens=2600).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt).strip()
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        a, b = txt.find("["), txt.rfind("]")
        data = json.loads(txt[a:b + 1]) if a != -1 and b != -1 else []
    out = [c for c in data if isinstance(c, dict) and c.get("theme")]
    log.info("mv_discovery proposed %d concepts", len(out))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="MV concept discovery — propose from real material")
    ap.add_argument("-n", type=int, default=4)
    ap.add_argument("--theme", default=None)
    args = ap.parse_args()
    for i, c in enumerate(propose_mv_concepts(args.n, theme=args.theme)):
        print(f"\n[{i}] {c.get('title')}  ({c.get('genre_lane')} / {c.get('vocal_mood')})")
        print(f"    theme : {c.get('theme')}")
        print(f"    log   : {c.get('logline')}")
        print(f"    kill  : {c.get('killpoint')}")
        print(f"    from  : {c.get('referenced_material')}")
        print(f"    why   : {c.get('why_mv')}")
