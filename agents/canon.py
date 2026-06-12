"""Central character canon — the ONE source of truth for the drift-prone facts.

Why this exists (PD 2026-06-09): character facts (Leo's eye color, the pets'
ages/sex, Ryani's tail/blaze) used to be copy-pasted across cameraman.py,
generate_character_scene.py, arc.py and several prompt .md files. When a fact
was corrected (the 2026-05-30 "gold-amber → chartreuse" Leo eye fix) it reached
ONE file and stayed stale in five for nine days, silently feeding the wrong
trait downstream.

Rule now: edit a character fact HERE, once. Every Python consumer imports its
block from this module, so a correction propagates everywhere. The companion
guard `scripts/check_canon.py` fails if a stale value (e.g. affirmative "amber
eyes" for Leo) reappears anywhere, so drift can't silently come back.

Phase 1 (this file): the blocks below are the verbatim authoritative text; the
Python consumers (cameraman markings, generate_character_scene image canon,
arc.CHARACTER_FACTS) import them. The prompt .md files still carry their own
copies for now but are guard-protected. Phase 2 will runtime-inject
`canon_md_block()` into those prompts so the markdown stops duplicating too.

Room / background canon lives in `data/set_library.json` (already central and
read at runtime) — do NOT duplicate it here. This file is characters + the
universal pet-rendering guardrails only.
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────
# Atomic facts — the values that have actually drifted. Reference these in
# prose/guards so the canonical value has exactly ONE definition.
# ──────────────────────────────────────────────────────────────────────
RYANI = {
    "name_ko": "랴니",
    "species_en": "black French Bulldog",
    "sex_en": "SPAYED FEMALE",      # she/her — NEVER male, no male anatomy
    "sex_ko": "암컷(중성화)",
    "age_years": 11,                 # NEVER "막내"/young/8개월
    "tail": "NO tail",               # French Bulldog — never render a tail
    "blaze": "THIN narrow white Boston-Terrier-style blaze",  # a fine line, NOT a wide splash
    "ears": "large UPRIGHT bat ears (erect, pointed up — NOT folded rose ears)",  # PD 2026-06-10
}
LEO = {
    "name_ko": "레오",
    "species_en": "orange tabby cat",
    "sex_en": "MALE",                # he/him
    "sex_ko": "수컷",
    "age_months": 8,                 # NEVER "veteran"/senior/11년차
    "eyes_en": "pale yellow-green / chartreuse",  # NOT gold, NOT amber
}

# ──────────────────────────────────────────────────────────────────────
# Rendered blocks — authoritative text. Edit HERE; consumers import these.
# ──────────────────────────────────────────────────────────────────────

# cameraman.py per-cut Seedance marking injection (note the leading space —
# it is appended to a motion_prompt).
RYANI_MARKING = (
    " CRITICAL — Ryani the black French Bulldog must keep her exact markings every "
    "frame: a THIN narrow white Boston-Terrier-style blaze (a fine line, NOT a wide "
    "splash, do NOT enlarge it) from nose up the forehead, silver-grey aged muzzle, "
    "white chin, white chest patch, large UPRIGHT "
    "bat ears (erect/pointed up, NOT folded rose ears), NO "
    "tail. Her BACK, neck and spine are SOLID BLACK — NO white stripe or line down "
    "the back/neck/spine (the only white is the FOREHEAD blaze + chin + chest + toes). "
    "Only black/white/grey — no brown. **NO painted white dot/spot on or above the "
    "eyes** (PD 2026-06-12: she has no eye dots — the eye area stays solid black; do "
    "NOT add eyebrow dots). Keep the blaze thin and the face "
    "identical to the input; do not redraw or distort her markings.")

LEO_MARKING = (
    " CRITICAL — Leo the orange tabby cat must look like the REAL cat, not AI-"
    "generated: pale yellow-green / chartreuse eyes (NOT gold or amber), white chin "
    "tuft, lean young-adult body, natural real-cat face and proportions. Do not "
    "warp, plasticize, or redraw his face.")

# generate_character_scene.py image-generation identity canon.
RYANI_IMAGE_CANON = (
    "Ryani — REAL black French Bulldog, SPAYED FEMALE (she/her, 11-year-old "
    "senior; clearly female, NO male anatomy). Markings (keep EXACTLY, do not "
    "redraw): a THIN NARROW white blaze (a fine Boston-Terrier line from nose up "
    "the forehead — NOT a wide splash, do NOT enlarge it), silver-grey aged muzzle, "
    "white chin, white chest patch, white toes, NO white dot/spot above or on the eyes, "
    "large UPRIGHT bat ears (erect/pointed up, NOT folded rose ears), ABSOLUTELY NO "
    "TAIL. Her BACK, neck and spine are SOLID BLACK — NO white stripe/line down the "
    "back or neck (white is FOREHEAD blaze + chin + chest + toes ONLY). "
    "Only black/white/grey — NO brown. Petite, "
    "refined, feminine build (NOT a muscular barrel-chested male). A REAL dog, not "
    "a cartoon.")

LEO_IMAGE_CANON = (
    "Leo — REAL orange tabby cat, MALE (he/him, young ~8 months). Pale "
    "YELLOW-GREEN / chartreuse eyes (NOT amber, NOT gold), white chin tuft, white "
    "whiskers, lean agile young-adult body, paler cream-orange cheeks and belly "
    "than the back. A REAL cat, not a cartoon.")

# arc.py showrunner authority block (Korean). Personality/ability/fear facts.
CHARACTER_FACTS = (
    "## 캐릭터 사실 (권위 — 여기 없는 성격/능력/공포는 발명 금지)\n"
    "⚠️ **종 절대 혼동 금지: 레오 = 고양이(cat, 주황 태비). 랴니 = 개(dog, 프렌치불독). "
    "절대 뒤바꾸지 마라.** 레오를 개로/랴니를 고양이로 쓰면 치명적 오류. "
    "('랴니엄마' = 레오가 랴니(개)를 부르는 애칭, 사람도 고양이도 아님.)\n"
    "- **레오(레오)**: 8개월 **수컷** 고양이(주황 태비). 2025-11-15 떠돌이로 구조됨 → "
    "랴니를 엄마로 여김('랴니엄마'는 레오 POV 호칭). 장난꾸러기·사냥꾼·매복 전문. "
    "세차를 무서워함. 고양이라 물을 피하고 물가에서 구경하는 쪽.\n"
    "- **랴니(랴니)**: 11살 **암컷(중성화)** 프렌치불독, 꼬리 없음. 의젓한 누나/엄마, "
    "차분·현명. ★ **물을 엄청 좋아하는 '물 매니아'**: 물만 보면 흥분해서 짖고, 특히 "
    "**고무호스/분수** 물을 보면 격하게 흥분해 **분수에 뛰어들려고 난리**. **수영도 아주 잘함"
    "('펠프스급')**. 겨울엔 **눈을 좋아하고 얼음 썰매를 탄다**. (거짓 금지: '랴니 물 공포/물 "
    "무서워함'은 완전히 틀림 — 정반대. 단 2016 아기 시절엔 잠깐 무서워했음 → 과거 회상에서만.) "
    "세차도 안 무서워함(레오와 대비).\n"
    "- **여름 물놀이/분수/수영 + 겨울 눈/얼음썰매 컨셉의 주인공 = 랴니.** 레오는 물가 구경/마른 쪽.\n"
    "- **간식·먹거리 (PD 2026-06-12, 사실 — 지어내지 말 것)**: "
    "★레오 = **츄르(고양이 간식 튜브)** 좋아함, **부추**도 먹음. **그릭요거트는 안 먹음.** "
    "★랴니 = **그릭요거트** 잘 먹음, 11살 **노령견이라 관절 영양제**를 챙겨먹음"
    "(츄르처럼 생긴 긴 튜브형 페이스트). 바닥 부스러기도 좋아함(성격 설명). "
    "⚠️ **그러나 '바닥에 떨어진 사료/부스러기를 먹는' 장면은 컨셉이 명시적으로 그걸 요구할 때만** "
    "화면에 넣어라 — 기본/디폴트 동작으로 자동 삽입 금지. (Seedance가 무슨 컨셉이든 자꾸 "
    "'랴니가 바닥 핥기'로 빠지는 경향이 있다. 츄르/튜브 핥기·요거트 먹기 등 지정된 동작이 있으면 "
    "**손에 든 튜브/그릇에서** 먹는 것이지 바닥이 아니다.) "
    "★랴니 **봄 취미 = 산책 나가서 개망초(망초) 어린잎을 뜯어 먹는 것**. "
    "→ '둘이 각자 간식' 컨셉: 레오=츄르/부추, 랴니=그릭요거트/관절영양제튜브. 서로 안 먹는 것 바꿔주지 마라.\n"
    "- **첫 카페 방문 = 랴니 2015년 / 레오 2025년** (10년 차이). 둘의 '공동 첫 카페/첫 경험'은 "
    "존재하지 않으니 지어내지 마라.\n"
    "- ⚠️ 위 목록에 없는 공포·능력·트레잇을 새로 지어내지 마라. 나이도 정확히(레오 8개월/"
    "랴니 11살) — 뒤바꾸지 마라.\n"
)

# Universal pet-rendering guardrails (injected into image/video prompts).
GUARD_NO_CLOTHING = (
    "Pets are bare-furred — NO clothing/hanbok/costumes (unless the scene "
    "explicitly says a harness).")
GUARD_NO_TEXT = (
    "Do NOT add any text, captions, watermarks, or logos to the image.")
GUARD_BG_STILLNESS = (
    "Background objects stay static — only the pets move.")


# Reviewer-facing appearance lines (Giri review prompt). SAME facts as the
# generation canon above — so the reviewer judges the SAME Ryani/Leo we generate.
# PD 2026-06-10: the reviewer used to keep its own copy that said Ryani "stocky
# compact body" while generation said "petite feminine" — the reviewer was grading
# a different dog. And it never flagged the distorted/melted photo_i2v faces (it
# passed a clearly-wrong Ryani face at 9/10). Both fixed here.
REVIEW_RYANI = (
    "**Ryani (French Bulldog, 11yr, SPAYED FEMALE)**: a THIN Boston Terrier-style "
    "WHITE BLAZE (a narrow line, NOT a wide splash) from nose to forehead, a white dot "
    "above each eye, silver-grey aged muzzle, white chin, large white chest patch, "
    "large UPRIGHT bat ears (erect/pointed up — NOT folded rose ears; flag folded "
    "ears as a defect), ABSOLUTELY NO TAIL (her rear is bare — flag any tail rendering as "
    "a major failure), petite refined feminine build (NOT a muscular barrel-chested "
    "male), only black/white/grey — no brown. Her BACK / neck / spine are SOLID BLACK "
    "— flag any white stripe or line down the back or neck as a failure (the white "
    "blaze is FOREHEAD-only). ALSO flag as a MAJOR failure any "
    "distorted / melted / uncanny face, mismatched or asymmetric eyes, or a floating "
    "white blob/orb artifact on the face — these are common when a still photo is "
    "animated (photo_i2v) and MUST lower the verdict, not pass.")
REVIEW_LEO = (
    "**Leo (orange tabby, ~8mo, MALE)**: pale yellow-green chartreuse eyes (NOT "
    "gold-amber), faint scar across nose bridge, white chin tuft. Tail often in a "
    "question-mark shape. Lean agile body, paler cream-orange cheeks/belly than the back.")


def image_canon(subjects: str) -> str:
    """Return the image-gen identity canon for 'leo' | 'ryani' | both."""
    s = (subjects or "").lower()
    if s == "leo":
        return LEO_IMAGE_CANON
    if s == "ryani":
        return RYANI_IMAGE_CANON
    return RYANI_IMAGE_CANON + " " + LEO_IMAGE_CANON


def canon_md_block() -> str:
    """Markdown rendering of the character canon, for Phase-2 runtime injection
    into the prompt .md files (writer/director/producer). Not yet wired."""
    return (
        "## 캐릭터 canon (권위 — 고치려면 agents/canon.py 한 곳만)\n"
        f"- **{LEO['name_ko']} (Leo)** — {LEO['species_en']}, {LEO['sex_en']} "
        f"(he/him), ~{LEO['age_months']}개월. Eyes: {LEO['eyes_en']} (NOT gold, NOT amber).\n"
        f"- **{RYANI['name_ko']} (Ryani)** — {RYANI['species_en']}, {RYANI['sex_en']} "
        f"(she/her), {RYANI['age_years']}살. {RYANI['tail']}; {RYANI['blaze']} "
        "(a fine line, NOT a wide splash).\n"
        f"- Guardrails: {GUARD_NO_CLOTHING} {GUARD_BG_STILLNESS} {GUARD_NO_TEXT}\n"
    )
