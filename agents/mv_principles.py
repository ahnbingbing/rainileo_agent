"""agents/mv_principles.py — the ONE source of MV quality principles.

Why this file exists (PD 2026-11-24): MV corrections kept being applied case-by-case —
a fix went into one generator's inline prompt, the checker never learned it, and the same
defect drifted back next MV. The daily lane solved this long ago: a shared standard that the
GENERATORS follow and a skeptical GATE (Giri) that ENFORCES, updated in lockstep. The MV lane
never got that. This module is it.

Two faces of the same principles:
  • MV_PRINCIPLES  — a prose block every MV generator (discovery / songwriter / director /
    still) injects into its system prompt, so it AVOIDS the defect at generation time.
  • the check_* functions — deterministic manifest checks agents/mv_reviewer.py runs so a defect
    that slips past generation is CAUGHT before PD ever sees it (score-cap pattern, like
    agents/reviewer.py's gates). Generator-fix + checker-fix stay in one place → no drift.

Each principle below traces to a real, repeated PD correction. Add a new one by stating the
PRINCIPLE (intent) + WHY, then — if it can be computed — a matching check_* gate. Prefer a
deterministic check over trusting the LLM; the whole point is that PD stops being the gate.
"""
from __future__ import annotations

import re

# ── the shared prose principles (injected into every MV generator) ────────────────────
MV_PRINCIPLES = """\
★MV 품질 원칙(생성 시 반드시 지켜라 — 어기면 리뷰 게이트가 반려한다):

1. 재료-진실 그라운딩: 없는 걸 있는 척 만들지 마라. real_story(rf_heavy)는 그 가사줄의 **구체적
   장소·동작의 실제 footage가 실제로 있을 때만** RF로 돌려라 — 피사체(랴니/레오)만 맞고 장소/계절이
   다르면(예: 여름 실내 클립을 '첫눈 창밖'에) RF가 아니라 AV다. 실내 라운징을 눈/창밖 가사에 붙이지
   말고, 못 채우는 beat는 AV로 라우팅하라. (근본: 흰 담요를 눈이라고 쓰면 안 된다.)

2. 다양성은 품질 바다: 같은 것을 반복하지 마라. 한 MV 안에서 같은 실 클립을 두 컷 이상에 쓰지 말고,
   컨셉·훅·제목 포맷·수사 장치도 과거 MV와 겹치지 마라. 몽타주가 같은 클립을 여러 가사줄에 붙이면
   '편집이 안 된' 걸로 읽힌다.

3. 피사체 스코프: 솔로 컷(subject=leo 또는 ryani)의 still_prompt·의상/아트디렉션은 **그 펫만** 지칭하라.
   "강아지와 고양이 둘 다" 같은 공용 문구를 솔로 컷에 쓰면 생성기가 없는 딴 펫을 불러들인다(래브라도 난입).

4. 캐릭터 락: 랴니=검은 무꼬리 프렌치불독(암컷)·**슬렌더 fine-boned 얼굴**(가는 볼·가는 주둥이, 스탠다드
   프불의 두꺼운 볼/주걱턱 아님)·가는 흰 블레이즈+작은 턱/가슴 흰 마킹. 레오=오렌지 태비, 랴니보다 약간 작음.
   종 혼동·꼬리·물공포(랴니는 물매니아)는 치명적 결함.

5. 싱크: 캡션은 곡의 실제 보컬 타임에 싱크되고, **각 컷의 화면도 그 가사줄이 불릴 때 떠야 한다**. 곡 인트로
   (보컬 전 구간)는 첫 컷이 흡수한다 — 컷 시퀀스가 캡션보다 앞서거나 뒤처지면 안 된다.

6. 지정값 존중: 컨셉이 명시한 값(music_genre·killpoint 등)을 자유롭게 바꾸지 말고 살려라. 후렴은 그 곡에서
   가장 기억에 남는 따라부르기 쉬운 훅이어야 한다.

7. 디자인은 1급 요소: 타이틀은 곡 무드에 맞춰 설계하고(겨울=눈 아트), 펫 얼굴을 가리지 마라. 나중에 붙이는
   자막 취급 금지.
"""


# ── setting vocabulary (for the RF setting-grounding check) ───────────────────────────
# A beat's lyric/action asserts a SETTING; the matched clip's scene must not contradict it.
_OUTDOOR = ("눈", "눈밭", "눈길", "설경", "첫눈", "산책", "골목", "거리", "공원", "들판", "야외", "밖",
            "창밖", "호수", "물가", "바다", "계곡", "마당", "길", "snow", "outdoor", "park", "street")
_INDOOR = ("실내", "집 안", "거실", "침대", "침실", "소파", "쿠션", "방석", "담요", "바닥", "마루",
           "주방", "식탁", "창가", "창틀", "방 안", "indoor", "bed", "couch", "sofa", "blanket", "cushion",
           "floor", "kitchen")


def _tokens(*vals: str) -> str:
    return " ".join(v for v in vals if v).lower()


def _setting_of(text: str) -> set[str]:
    """Which coarse settings a text asserts (outdoor/indoor/snow). Snow is tracked separately
    because 'snow outside a window' is outdoor-content seen from indoors — only a clip with NO
    snow/outdoor signal at all contradicts a snow beat."""
    t = _tokens(text)
    out: set[str] = set()
    if any(k in t for k in ("눈", "snow", "설경", "첫눈")):
        out.add("snow")
    if any(k in t for k in _OUTDOOR):
        out.add("outdoor")
    if any(k in t for k in _INDOOR):
        out.add("indoor")
    return out


# ── deterministic checks (each mutates `report`, score-cap + verdict, like Giri gates) ──
_PASS = ("업로드", "즉시 업로드", "소폭 수정 후 업로드")


def _cap(report: dict, score: int, note: str) -> None:
    report["점수"] = min(int(report.get("점수", 10) or 10), score)
    if report.get("판정") in _PASS or not report.get("판정"):
        report["판정"] = "수정 필요"
    report.setdefault("issues", []).append(note)


def check_rf_clip_diversity(m: dict, report: dict) -> None:
    """Principle 2. No real clip may back more than one cut (montage repetition reads as 'unedited')."""
    seen: dict[str, list[str]] = {}
    for c in m.get("cuts", []):
        if c.get("cut_type") != "rf":
            continue
        aid = c.get("rf_asset_id")
        if aid:
            seen.setdefault(aid, []).append(c.get("tag", "?"))
    dupes = {a: tags for a, tags in seen.items() if len(tags) > 1}
    if dupes:
        worst = max(dupes.values(), key=len)
        _cap(report, 5, f"RF 클립 재탕 — 한 클립이 {len(worst)}컷에 재사용({', '.join(worst)})")


def check_rf_setting_grounding(m: dict, report: dict) -> None:
    """Principle 1. An RF cut whose lyric asserts an OUTDOOR/snow setting but whose matched clip's
    scene is purely INDOOR (no snow/outdoor signal) is the 'indoor-as-snow' lie → that beat should
    have been AV. Only flag a hard contradiction (beat=outdoor/snow, clip=indoor-only)."""
    for c in m.get("cuts", []):
        if c.get("cut_type") != "rf":
            continue
        beat = _setting_of(_tokens(c.get("lyric"), c.get("action"), c.get("setting")))
        scene = ""
        cands = c.get("rf_candidates") or []
        if cands:
            scene = cands[0].get("scene", "")
        clip = _setting_of(scene)
        wants_outdoor = ("outdoor" in beat) or ("snow" in beat)
        clip_indoor_only = ("indoor" in clip) and not ({"outdoor", "snow"} & clip)
        if wants_outdoor and clip_indoor_only:
            _cap(report, 5, f"RF 설정 불일치 — {c.get('tag')}: 가사는 눈/실외인데 클립은 실내({scene[:40]})")


def check_caption_sync(m: dict, report: dict, tol: float = 1.2) -> None:
    """Principle 5. Coverage invariant: while caption line i is being SUNG, cut i must be the one
    ON SCREEN — i.e. cut i's video span [vstart, vend] must cover line i's [start, end] (±tol).
    (A cut legitimately STARTS before its caption — cut 0 absorbs the song's instrumental intro —
    so this checks coverage of the sung window, NOT start==start. The real desync is a cut that
    ENDS before its caption finishes → the next cut's picture plays under this line's caption.)"""
    kl = m.get("karaoke_lines") or []
    cuts = m.get("cuts", [])
    if not kl:
        return
    vstart = 0.0
    worst = None
    for i, c in enumerate(cuts):
        dur = float(c.get("duration") or 0.0)
        vend = vstart + dur
        if i < len(kl):
            kstart = float(kl[i].get("start") or 0.0)
            kend = float(kl[i].get("end") or kstart)
            # uncovered sung time = parts of [kstart,kend] outside [vstart,vend]. A cut starting
            # BEFORE its caption is fine (already on screen); only starting LATE or ending EARLY hurts.
            gap = max(0.0, vstart - kstart - tol) + max(0.0, kend - vend - tol)
            if gap > tol and (worst is None or gap > worst[1]):
                worst = (c.get("tag", "?"), gap, vstart, vend, kstart, kend)
        vstart = vend
    if worst:
        _cap(report, 6, f"캡션↔영상 싱크 — {worst[0]} 영상[{worst[2]:.1f}~{worst[3]:.1f}]가 "
                        f"가사[{worst[4]:.1f}~{worst[5]:.1f}]를 {worst[1]:.1f}s 못 덮음")


def check_solo_cut_foreign_pet(m: dict, report: dict) -> None:
    """Principle 3. A solo cut (subject leo|ryani) whose prompt text names BOTH a dog and a cat will
    hallucinate the absent pet. Deterministic prompt-text check (the generation-time root cause)."""
    style = (m.get("art_direction") or "").lower()
    dog = ("dog", "bulldog", "강아지", "불독", "frenchie")
    cat = ("cat", "tabby", "고양이", "레오")
    for c in m.get("cuts", []):
        subj = (c.get("subject") or "").lower()
        if subj not in ("leo", "ryani"):
            continue
        txt = (_tokens(c.get("still_prompt"), c.get("action")) + " " + style)
        names_dog = any(k in txt for k in dog)
        names_cat = any(k in txt for k in cat)
        if names_dog and names_cat:
            _cap(report, 5, f"솔로 컷에 딴 펫 호출 — {c.get('tag')}(subject={subj})인데 프롬프트가 개+고양이 둘 다 지칭")


def check_concept_freshness(m: dict, past_themes: list[str] | None, report: dict) -> None:
    """Principle 2 (concept level). The chosen theme must not heavily overlap a past MV's theme."""
    chosen = ((m.get("concept") or {}).get("chosen") or {}).get("theme") or ""
    if not chosen or not past_themes:
        return
    ct = set(re.findall(r"[가-힣a-z]+", chosen.lower()))
    for pt in past_themes:
        pset = set(re.findall(r"[가-힣a-z]+", pt.lower()))
        if not pset:
            continue
        overlap = len(ct & pset) / max(1, len(ct | pset))
        if overlap >= 0.5:
            _cap(report, 6, f"컨셉 신선도 — 과거 MV와 모티프 과다중첩({overlap:.0%}): '{pt[:30]}'")
            return
