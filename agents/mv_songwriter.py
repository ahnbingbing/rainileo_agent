#!/usr/bin/env python3
"""Stage 1a — MV songwriter (lyrics + Suno style variants).

Writes the lyrics for a chosen MV concept and proposes 2 DISTINCT Suno style prompts,
following the feedback-log §2 song rules so the Director (stage 4) and alignment
(stage 2) have what they need:

  • onomatopoeia / 의태어 the Director can turn into motion beats (톡톡/폴짝/골골골).
  • per-LINE subject (랴니 / 레오 / 둘이) — the基 of subject-matching (§4, §8 검수).
  • concise structure: uptempo/meme ~40s+ (verse + chorus×2 + outro hook), ballad ~40–50s.
    NOT 2–4 min; long songs break alignment and splicing (§2, mv_80s_version_via_cover).
  • vocal bucket from the §2.0 rubric (화자 캐릭터 × 무드), not a global default.
  • DISTINCT intro per song (Suno re-uses a stock intro if every style prompt looks alike).

This is LLM-only (no cost). gen_song (Suno, paid) runs AFTER PD reviews these lyrics —
the actual audio + PD variant pick happens at the song stage's song_pick gate.
"""
from __future__ import annotations

import json
import logging
import re

from agents.llm_cascade import call_text_cascade
from agents.concept_brainstorm import _character_facts

log = logging.getLogger("agents.mv_songwriter")

# §2.0 vocal buckets → Suno-safe timbre descriptors (artist names are rejected by Suno).
VOCAL_BUCKETS = {
    "cute_male": "young-adult male, mid-range, clear, bright, full, playful, earnest, boyish, smooth, crisp",
    "mature_male": "male tenor, warm, clear, bright, emotional, expressive, earnest, smooth, rich, deeper mature tone",
    "cute_female": "clear, pure, bright, sweet, warm, youthful, tender, earnest, smooth female vocal",
    "sultry_female": "powerful female vocal, husky, smoky, sultry, soulful, rich, full, belted, confident, sexy edge",
}


def write_lyrics(concept: dict, *, context: dict | None = None) -> dict:
    """Return {title, vocal_mood, vocal_timbre, lyrics, lyric_lines[], suno_styles[], structure_notes}.

    concept: the chosen discovery candidate (theme/logline/genre_lane/vocal_mood/
      story_beats/onomatopoeia_hooks/killpoint)."""
    facts = _character_facts()
    lane = concept.get("genre_lane", "ballad_rf")
    mood = concept.get("vocal_mood") or ("cute_female" if lane == "dance_av" else "cute_female")
    timbre = VOCAL_BUCKETS.get(mood, VOCAL_BUCKETS["cute_female"])

    lane_rule = (
        "댄스/힙합/밈 레인: 통통 튀는 업템포(밝고 따뜻·통통 쪽, 과한 쎈 일렉 금지 — PD 취향). "
        "목표 ~40~55s: [Intro 짧게]+[Verse]+[Chorus]+[Verse2 또는 Chorus 반복]+[Outro 훅 반복]. "
        "후렴은 따라부르기 쉬운 짧은 훅 + 의성어. 급히 끊기지 말고 아웃트로 훅까지 완결."
        if lane == "dance_av" else
        "발라드/미드 레인: 따뜻한 어쿠스틱 K-인디(피아노/스트링 과한 시네마틱보다 담백·따뜻). "
        "목표 ~40~50s: [Intro 짧게]+[Verse]+[Chorus]+[Outro]. 잔잔하고 정서적, 마지막 훅 여운."
    )

    # The discovery concept carries a music_genre (+ any PD rhythm/feel note) — that's a
    # deliberate choice about THIS song's genre and groove, not a free pick. Honor it in both
    # style prompts above the lane default; the lane only sets the broad energy band.
    genre = (concept.get("music_genre") or "").strip()
    genre_rule = (
        f"\n- ★곡 장르·리듬 지정(최우선): **{genre}**. 이 장르감·리듬(그루브/박자 느낌)을 두 Suno 스타일 "
        "프롬프트 모두에 명시적으로 반영하라 — 레인 기본값보다 이 지정이 우선이다. (예: '셔플 리듬'은 "
        "swing/triplet 그루브, '캐롤 느낌'은 종소리/글로켄슈필·따뜻한 겨울 캐롤 편곡으로 살려라.)"
        if genre else ""
    )

    system = (
        "너는 'Ryani & Leo' 오리지널 MV의 작사가다. 주어진 컨셉으로 **한국어 가사**를 쓴다.\n"
        f"캐릭터(고정): {facts}\n"
        "랴니=물매니아·무꼬리·의젓한 누나(암컷), 레오=2025-11-15 구조된 수컷 아기 고양이·장난꾸러기. 종 혼동 금지.\n\n"
        "★규칙(어기면 다시):\n"
        f"- {lane_rule}{genre_rule}\n"
        "- **후렴(Chorus)이 곡에서 가장 기억에 남는 부분이어야 한다** — 레인과 무관한 원칙. 발라드라도 후렴은 "
        "흥얼거려지는 짧고 또렷한 훅이어야 한다(한 번 듣고 따라 부를 멜로디 라인). 제목 문구나 시그니처 "
        "의성어(예: '펑펑', '딩동')를 후렴 훅으로 **반복**해 각인시키고, 벌스→후렴에서 멜로디가 확 올라가는 "
        "리프트를 둬라. 잔잔함과 기억성은 양립한다 — 잔잔하다고 훅이 밋밋하면 안 된다.\n"
        "- **의성어·의태어를 적극** 넣어라(톡톡/폴짝/데굴데굴/쭈욱/골골골 등) — Director가 그 순간을 동작 컷으로 살린다.\n"
        "- **각 줄의 주인공을 분명히**(랴니 / 레오 / 둘이) — 나중에 화면 펫 매칭의 기준. 가사 자체는 자연스럽게 쓰되 "
        "lyric_lines에 줄마다 subject를 태그하라.\n"
        "- 1인칭/화자 톤은 보컬 무드와 맞춰라(이 곡 보컬 = " + mood + ").\n"
        "- 길게 늘이지 마라(2~4분 금지). 간결·완결.\n"
        "- Suno 스타일 프롬프트 2개를 **서로 뚜렷이 다르게**(인트로·장르 뉘앙스 차이) 써라 — 둘이 비슷하면 Suno가 "
        "같은 스톡 인트로를 재탕한다. 아티스트명 금지, 사운드 특징으로만.\n\n"
        "출력(JSON 1개):\n"
        "{\n"
        '  "title": "곡 제목(한국어)",\n'
        '  "structure_notes": "길이·구성 한 줄",\n'
        '  "lyrics": "[Intro]\\n...\\n[Verse]\\n...\\n[Chorus]\\n...\\n[Outro]\\n...  (Suno에 넣을 전체 가사, 섹션 태그 포함)",\n'
        '  "lyric_lines": [{"text":"한 줄 가사","subject":"ryani|leo|both","onomatopoeia":"있으면 그 의성어/빈칸"}],  (섹션태그 제외, 부르는 순서대로)\n'
        '  "suno_styles": [\n'
        '     {"label":"A안 한줄설명","style":"Suno 스타일 프롬프트(장르·BPM·분위기·' + f'vocal: {timbre}' + '·짧은 인트로 명시)","intro_desc":"이 안의 독특한 인트로 한 줄"},\n'
        '     {"label":"B안 한줄설명","style":"...다른 장르뉘앙스...","intro_desc":"다른 인트로"}\n'
        "  ]\n"
        "}\n"
        "JSON만 출력.")

    beats = "\n".join(f"  - {b}" for b in (concept.get("story_beats") or []))
    ono = ", ".join(concept.get("onomatopoeia_hooks") or []) if isinstance(concept.get("onomatopoeia_hooks"), list) else (concept.get("onomatopoeia_hooks") or "")
    user = (f"컨셉 제목: {concept.get('title')}\n주제: {concept.get('theme')}\n"
            f"로그라인: {concept.get('logline')}\n레인: {lane}\n곡 장르·리듬: {genre or '(미지정 — 레인 기본)'}\n보컬 무드: {mood}\n"
            f"킬포인트(감정 정점): {concept.get('killpoint')}\n"
            f"의성어 후보: {ono}\n비트:\n{beats}\n\n이 컨셉으로 가사+스타일 2안을 JSON으로.")

    txt = call_text_cascade(system, user, max_tokens=2200).strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt).strip()
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        a, b = txt.find("{"), txt.rfind("}")
        data = json.loads(txt[a:b + 1]) if a != -1 and b != -1 else {}
    data.setdefault("vocal_mood", mood)
    data.setdefault("vocal_timbre", timbre)
    log.info("mv_songwriter: '%s', %d lines, %d style variants",
             data.get("title"), len(data.get("lyric_lines", [])), len(data.get("suno_styles", [])))
    return data


if __name__ == "__main__":
    import argparse
    from agents.mv_producer import load_manifest
    ap = argparse.ArgumentParser(description="MV songwriter — lyrics + Suno styles for a picked concept")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()
    m = load_manifest(args.slug)
    concept = (m or {}).get("concept", {}).get("chosen") or {}
    if not concept:
        raise SystemExit("no chosen concept in manifest — pick one first")
    d = write_lyrics(concept)
    print(f"\n=== {d.get('title')}  ({d.get('vocal_mood')}) ===")
    print(d.get("structure_notes"), "\n")
    print(d.get("lyrics"))
    print("\n--- subject-tagged lines ---")
    for ln in d.get("lyric_lines", []):
        print(f"  [{ln.get('subject'):4}] {ln.get('text')}" + (f"   ({ln.get('onomatopoeia')})" if ln.get('onomatopoeia') else ""))
    print("\n--- Suno style variants ---")
    for s in d.get("suno_styles", []):
        print(f"  [{s.get('label')}] intro: {s.get('intro_desc')}\n     {s.get('style')}")
