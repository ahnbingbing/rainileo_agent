#!/usr/bin/env python3
"""Stage 4 — MV Director (per-cut cue-sheet).

Turns the chosen concept + lyrics into a tight cut list, mapping each lyric line /
onomatopoeia to a specific shot (feedback-log §4). Output drives the still board
(stage 5) and Seedance render (stage 6).

Contracts baked in (feedback-log):
  • Tight mapping — a cut per lyric beat, onomatopoeia → a VISIBLE move (§4; 쭉쭉→stretch,
    실룩실룩→hip wiggle, 뒹굴뒹굴→belly roll, 빙글→spin).
  • Subject matching — each cut's on-screen pet = that line's subject (§4, §8 검수).
  • Shot/colour variety — mix WS/MS/CU/ECU/insert, change colour cut-to-cut (§4 색 다양성).
  • Real four-legged animals, NO anthro (§8.13); NO humans in clean dance cuts (§8.14).
  • CHEST-UP / lower body out of frame where a pose would expose the groin (§6 anatomy).
  • dance_av meme → repeatable signature moves the viewer can copy (§4 "따라할 meme 댄스");
    stylised bright stage (AV justifies its lane by doing what RF can't).
Each cut carries BOTH a `still_prompt` (full scene for gpt-image-2) and a `motion_prompt`
(what moves, for Seedance i2v later).
"""
from __future__ import annotations

import json
import logging
import re

from agents.llm_cascade import call_text_cascade
from agents.concept_brainstorm import _character_facts
from agents.mv_principles import MV_PRINCIPLES

log = logging.getLogger("agents.mv_director")


def direct_cuts(concept: dict, lyrics_data: dict, rf_weight: str = "rf_heavy",
                *, music_genre: str = "dance", n_cuts: int = 24,
                homage: str | None = None) -> list[dict]:
    """Return a cut list. Each cut is routed RF or AV per `rf_weight` (PD content-origin rule):
      • RF cut  → cut_type='rf', with `clip_query` (search the real library for this moment).
      • AV cut  → cut_type='av', with `still_prompt`+`motion_prompt` (gpt-image-2 → Seedance).
    rf_heavy → most cuts RF (the real moments are the backbone), few AV for concept/stage/title."""
    facts = _character_facts()
    lines = lyrics_data.get("lyric_lines", [])
    lines_txt = "\n".join(
        f"  {i+1}. [{ln.get('subject','both')}] {ln.get('text','')}"
        + (f"  (의성어: {ln.get('onomatopoeia')})" if ln.get("onomatopoeia") else "")
        for i, ln in enumerate(lines))

    route_rule = (
        "★RF/AV 라우팅(rf_heavy): 이 MV는 **실제 있던 이야기**라 **실사(RF)가 뼈대**다. "
        "가사 비트 대부분은 **실제로 일어난 순간**(랴니 개구리자세·레오 뒹굴·배 까기·play-bow·둘이 같이 "
        "있는 투샷)이니 **cut_type='rf'**로 두고, 그 순간을 라이브러리에서 찾을 `clip_query`(영어, 구체 "
        "동작+주인공)를 써라. **AV(cut_type='av')는 실사로 못 찍는 컨셉/스테이지/타이틀 프레이밍 컷에만** "
        "소수(예: '챔피언스 무대', 트로피, 스포트라이트 인트로). 억지로 AV 늘리지 마라 — 실사가 있으면 RF."
        if rf_weight == "rf_heavy" else
        "★RF/AV 라우팅(av_heavy): 이 MV는 **지어낸 컨셉/스펙터클**이라 **AV가 뼈대**(cut_type='av', "
        "still_prompt+motion_prompt). 실사(cut_type='rf', clip_query)는 진짜 순간이 컷을 살릴 때만 양념으로."
        if rf_weight == "av_heavy" else
        "★RF/AV 라우팅(balanced): 실제 순간은 RF(clip_query), 지어낸 컨셉은 AV(still_prompt)로 반반 섞어라."
    )
    energy = ("업템포 댄스/밈 — 따라할 반복 시그니처 무브, 타이트 컷."
              if music_genre in ("dance", "hiphop", "pop") else "잔잔·따뜻한 정서.")

    # Reference-directing analysis (stage_reference → homage.doc): real reference MVs
    # analyzed into a directing standard. When present it is the TASTE FLOOR — the cue-sheet
    # must homage its palette / signature shots / mood, not invent a weaker look. Grounds the
    # shotlist in a proven aesthetic instead of generic "pretty" cuts.
    homage_block = (
        f"★레퍼런스 디렉팅 기준(이 기준을 homage하라 — 팔레트·시그니처 샷·무드를 이 아래로 떨어뜨리지 마라):\n"
        f"{homage.strip()}\n\n"
        if homage and homage.strip() else "")

    system = (
        "너는 'Ryani & Leo' 오리지널 MV의 **감독(콘티)**이다. 컨셉과 가사를 받아 "
        f"컷별 큐시트 {n_cuts}개 내외를 짠다(가사 비트에 타이트 매핑).\n"
        f"캐릭터(고정): {facts}\n"
        "랴니=검정 프렌치불독·무꼬리·흰 블레이즈/턱/가슴/발끝·슬림, 레오=오렌지 태비·샤르트뢰즈 눈·흰 턱. "
        "종/무꼬리/마킹 어기면 치명.\n\n"
        f"{route_rule}\n에너지: {energy}\n\n"
        + homage_block
        + MV_PRINCIPLES + "\n\n"
        "★콘티 규칙(어기면 다시):\n"
        "- **★숏츠 페이싱(가장 중요 — 어기면 시청자가 바로 다음 숏츠로 넘어간다)**: 컷은 짧고 타이트하게, "
        "대부분 2~4초 느낌. **한 가사줄을 한 컷으로 길게 늘이지 마라** — 길게 불리는 줄은 **2~3컷으로 쪼개라**: "
        "같은 가사줄(lyric 필드를 **똑같이 복사**)에 서로 다른 순간·샷사이즈·앵글·액션을 줘라(그래야 타이밍 엔진이 "
        "그 줄 시간을 여러 컷에 나눠 빠른 컷감을 만든다). **오프닝(첫 1~2컷)은 느린 와이드 글라이드 금지** — 즉시 훅: "
        "타이트 CU·빠른 시그니처 모션·강한 비트 히트로 0.5초 만에 붙잡아라. 컷마다 **다른 액션**(같은 동작·같은 구도 "
        "반복 금지 — 반복은 지루함으로 읽힌다). CU/인서트/디테일(발·눈·핸들·네온 반사)을 자주 끼워 리듬을 끊어라.\n"
        "- **의성어 → 보이는 동작**: 쭉쭉=기지개 스트레치, 실룩실룩=엉덩이 흔들, 뒹굴뒹굴=배 보이며 구르기, "
        "빙글=제자리 회전, 톡=앞발 톡. 가사 그 순간에 그 동작 컷.\n"
        "- **주인공 매칭**: 컷의 화면 펫 = 그 가사 줄 subject(ryani/leo/both). 랴니 줄엔 랴니, 레오 줄엔 레오.\n"
        "- **가사 그대로 그려라(문자적 그라운딩)**: still_prompt/clip_query는 그 가사 줄이 말하는 구체물을 "
        "그대로 담아라 — 장소·사물·누가 어디에 있는지. 더 안전한 근사치로 바꾸지 마라(그러면 화면이 가사를 "
        "배신한다). 예: '레오는 식탁 위에서' → 레오가 **식탁 상판 위에** 올라가 있는 컷(옆 의자 위가 아님). "
        "'고구마' → 진짜 군고구마가 보이게(다른 간식 대체 금지).\n"
        "- **★연속성 LOCK(드리프트 방지)**: 모든 컷이 공유하는 하나의 물리 세계를 `art_direction`에 못박아라 — "
        "반복 등장하는 소품/탈것은 **단 하나의 같은 개체**(같은 차종·같은 색·같은 핸들 방향 LEFT-HAND DRIVE, "
        "오픈카면 전 컷 오픈카·클로즈면 전 컷 클로즈), **누가 어디 앉는지 고정**(예: 랴니=운전석 LEFT, 레오=조수석 "
        "RIGHT를 전 컷 유지), **시간대·팔레트 고정**(네온 밤이면 전 컷 네온 밤 — 노을/낮/골드 지평선 금지). "
        "각 컷 still_prompt는 이 lock과 모순되면 안 된다(핸들 좌→우, 오픈→클로즈, 밤→노을 금지). "
        "샷·색 **다양성은 이 lock 안에서**(앵글·구도·네온 색조는 바꾸되 세계는 동일).\n"
        "- **샷 다양성**: WS/MS/CU/ECU/인서트 섞되, 위 연속성 lock을 깨지 않는다.\n"
        "- **해부 안전**: 뒷다리/배를 세워 보이는 포즈는 **가슴 위(chest-up) 타이트 프레이밍**으로 하반신 프레임 밖.\n"
        "- **실제 네발 동물**(사람처럼 서서 물건 들기 금지), 깨끗한 댄스 컷엔 **사람 없음**.\n"
        "- 의상 = 자연 털 기본(이 컨셉은 코스튬 아님). 무대/배경만 스타일링.\n"
        "- 킬포인트(감정 정점) 컷 1개를 is_killpoint=true로 표시하고 더 공들인 샷으로.\n\n"
        "출력 = JSON 오브젝트 {\"art_direction\": \"...\", \"cuts\": [ ... ]}.\n"
        '"art_direction" = 전 컷 공유 연속성 LOCK을 영어 한 단락으로: 그 하나의 탈것/소품(차종·색·'
        'LEFT-HAND DRIVE 핸들·오픈/클로즈)·좌석 배치(누가 LEFT, 누가 RIGHT)·시간대/팔레트를 명시('
        'gpt-image가 매 컷 동일하게 유지하도록). 예: "The SAME single retro 1980s open-top convertible '
        'in every shot — deep navy blue, chrome trim, LEFT-HAND DRIVE (steering wheel on the LEFT). '
        'Ryani always in the driver seat (LEFT), Leo always in the passenger seat (RIGHT). Always deep '
        'neon night — blue/violet/cyan city glow, never sunset/day/gold horizon."\n'
        '"cuts" = 큐시트 배열. 각 컷 = {\n'
        '  "tag": "c01_...", "lyric": "이 컷이 받는 가사 줄(또는 비트)",\n'
        '  "subject": "ryani|leo|both",\n'
        '  "cut_type": "rf|av",   // RF=실제 클립, AV=gpt-image-2→Seedance (위 라우팅 규칙대로)\n'
        '  "shot_size": "WS|MS|CU|ECU|insert", "camera_move": "slow push-in|orbit|tilt-up|static|...",\n'
        '  "action": "화면에서 일어나는 동작(의성어→동작)",\n'
        '  "setting": "배경/무대/색",\n'
        '  "clip_query": "[cut_type=rf일 때만] 라이브러리에서 이 순간을 찾을 영어 검색어(구체 동작+주인공, 예: \\"black French bulldog lying with legs splayed in a frog stretch\\")",\n'
        '  "still_prompt": "[cut_type=av일 때만] gpt-image-2용 장면 묘사(영어, ONE concise sentence — 포즈·구도·색·배경)",\n'
        '  "motion_prompt": "[cut_type=av일 때만] Seedance i2v용 — 무엇이 어떻게 움직이는지(영어, ONE short phrase)",\n'
        '  "is_killpoint": false\n'
        "}.\n"
        "간결하게. 위 {art_direction, cuts} JSON 오브젝트만 출력.")
    genre_lane = concept.get("genre_lane") or ("ballad_rf" if rf_weight == "rf_heavy" else "dance_av")
    user = (f"곡 제목: {lyrics_data.get('title')}\n컨셉: {concept.get('title')} — {concept.get('logline')}\n"
            f"레인: {genre_lane}\n킬포인트: {concept.get('killpoint')}\n\n가사(줄별 주인공·의성어):\n{lines_txt}\n\n"
            f"큐시트 {n_cuts}개 내외 + art_direction 연속성 lock을 JSON 오브젝트로.")

    txt = call_text_cascade(system, user, max_tokens=8000).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt).strip()
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        # object first ({art_direction, cuts}), fall back to a bare array
        a, b = txt.find("{"), txt.rfind("}")
        try:
            data = json.loads(txt[a:b + 1]) if a != -1 and b != -1 else []
        except json.JSONDecodeError:
            a, b = txt.find("["), txt.rfind("]")
            data = json.loads(txt[a:b + 1]) if a != -1 and b != -1 else []
    if isinstance(data, dict):
        art_direction = (data.get("art_direction") or "").strip()
        raw_cuts = data.get("cuts") or []
    else:  # legacy bare array
        art_direction, raw_cuts = "", data
    cuts = []
    for i, c in enumerate(raw_cuts):
        if not isinstance(c, dict):
            continue
        c.setdefault("tag", f"c{i+1:02d}")
        cuts.append(c)
    log.info("mv_director: %d cuts, art_direction=%s", len(cuts), bool(art_direction))
    return cuts, art_direction


if __name__ == "__main__":
    import argparse
    from agents.mv_producer import load_manifest
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("-n", type=int, default=14)
    args = ap.parse_args()
    m = load_manifest(args.slug)
    concept = m["concept"]["chosen"]
    ld = m["song"]["lyrics_data"]
    cuts, art_direction = direct_cuts(concept, ld, m.get("rf_weight", "rf_heavy"),
                                      music_genre=m.get("music_genre", "dance"), n_cuts=args.n)
    print(f"[art_direction] {art_direction}\n")
    for c in cuts:
        kp = " ★KILLPOINT" if c.get("is_killpoint") else ""
        print(f"\n[{c['tag']}] {c.get('cut_type','av').upper()} / {c.get('shot_size')} / {c.get('subject')}{kp}")
        print(f"    lyric : {c.get('lyric')}")
        print(f"    action: {c.get('action')}")
        if c.get("cut_type") == "rf":
            print(f"    clip? : {c.get('clip_query')}")
        else:
            print(f"    still : {c.get('still_prompt')}")
