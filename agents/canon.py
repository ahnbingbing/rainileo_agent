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
    "exists_from": "2015-05-05",     # born — cannot appear in footage before this
}
LEO = {
    "name_ko": "레오",
    "species_en": "orange tabby cat",
    "sex_en": "MALE",                # he/him
    "sex_ko": "수컷",
    "age_months": 8,                 # NEVER "veteran"/senior/11년차
    "eyes_en": "pale yellow-green / chartreuse",  # NOT gold, NOT amber
    # Born ~2025-09-25, rescued 2025-11-15. He CANNOT appear in any earlier footage —
    # an orange cat in pre-2025 clips is a different/stray cat, NOT Leo. (PD 2026-06-22:
    # a 2020 clip got captioned "5년 전 레오"; this date is the machine-usable boundary
    # the VLM tagger + subject guard read so that can't happen again.)
    "exists_from": "2025-09-25",
}


def pet_exists_on(pet: str, captured_iso: "str | None") -> bool:
    """Could `pet` (canonical key 'ryani'/'leo') appear in footage captured at
    captured_iso? Missing/blank date or unknown pet → True (never strip on no data).
    The single source for temporal subject grounding — VLM tagger, the producer RF
    subject guard, and any caption check all read this, so the boundary is defined once."""
    if not captured_iso:
        return True
    ef = {"ryani": RYANI, "leo": LEO}.get((pet or "").strip().lower(), {}).get("exists_from")
    return True if not ef else str(captured_iso)[:10] >= ef

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
    "Only black/white/grey — no brown. Above each eye she has a FAINT, subtle "
    "eyebrow-like white mark (small and thin, brow-like — present but understated; "
    "NOT a bold or large round dot). "
    "SIZE (PD 2026-06-21): she is a stocky, solid adult French Bulldog — in any frame "
    "shared with Leo the cat she reads HEAVIER and a bit LARGER than him (he is a small "
    "lean young cat), never smaller than the cat. "
    "★The center forehead blaze must stay a THIN pencil-width line in EVERY cut — it "
    "must NEVER thicken or widen into a broad white stripe/patch (a thick or wide "
    "center blaze is WRONG; keep it a fine narrow line). Keep the face "
    "identical to the input; do not redraw or distort her markings."
    " POSE = MATCH THE CUT'S ACTION (PD 2026-06-12): render exactly the action this "
    "cut describes (e.g. splashing in waves, sitting, looking up, leaping, being held). "
    "Do NOT auto-insert a nose-down sniffing/licking-the-floor pose when the action is "
    "something else — that floor-sniffing pose is correct ONLY when THIS cut's action "
    "actually calls for it. When the action isn't about the floor, keep her head up.")

LEO_MARKING = (
    " CRITICAL — Leo the orange tabby cat must look like the REAL cat, not AI-"
    "generated: pale yellow-green / chartreuse eyes (NOT gold or amber), white chin "
    "tuft, lean young-adult body, natural real-cat face and proportions. Do not "
    "warp, plasticize, or redraw his face."
    " SIZE (PD 2026-06-21): Leo is a SMALL, slim young cat (~8 months, ~3kg) — when he "
    "shares the frame with Ryani he must read NOTICEABLY SMALLER and lighter than her: "
    "she is a stocky, heavy adult French Bulldog and he is a lean young cat. Do NOT "
    "render Leo large, chunky, or bigger than the dog.")

# generate_character_scene.py image-generation identity canon.
RYANI_IMAGE_CANON = (
    "Ryani — REAL black French Bulldog, SPAYED FEMALE (she/her, 11-year-old "
    "senior; clearly female, NO male anatomy). Markings (keep EXACTLY, do not "
    "redraw): a THIN NARROW white blaze (a fine Boston-Terrier line from nose up "
    "the forehead — NOT a wide splash, do NOT enlarge it), silver-grey aged muzzle, "
    "white chin, white chest patch, white toes, a FAINT subtle eyebrow-like white mark "
    "above each eye (small/thin, brow-like — NOT a bold round dot), "
    "large UPRIGHT bat ears (erect/pointed up, NOT folded rose ears), ABSOLUTELY NO "
    "TAIL. Her BACK, neck and spine are SOLID BLACK — NO white stripe/line down the "
    "back or neck (white is FOREHEAD blaze + chin + chest + toes ONLY). "
    "Only black/white/grey — NO brown. Petite, "
    "refined, feminine build (NOT a muscular barrel-chested male) — but still a stocky, "
    "solid adult French Bulldog: in any two-shot with Leo she is HEAVIER and a bit "
    "LARGER than the small young cat, never smaller than him. A REAL dog, not "
    "a cartoon. POSE = MATCH THE SCENE'S ACTION (PD 2026-06-12): render exactly the "
    "action this cut describes (splashing in waves, sitting, looking up, leaping, "
    "being held, etc.). Do NOT auto-insert a nose-down sniffing/licking-the-floor "
    "pose when the action is something else — floor-sniffing is correct ONLY when "
    "this scene's action actually calls for it; otherwise keep her head/face up.")

LEO_IMAGE_CANON = (
    "Leo — REAL orange tabby cat, MALE (he/him, young ~8 months). Pale "
    "YELLOW-GREEN / chartreuse eyes (NOT amber, NOT gold), white chin tuft, white "
    "whiskers, lean agile young-adult body, paler cream-orange cheeks and belly "
    "than the back. A REAL cat, not a cartoon. SIZE: a SMALL slim young cat (~3kg) — "
    "in any shot with Ryani he is NOTICEABLY SMALLER and lighter than her stocky adult "
    "French Bulldog build; never render him large, chunky, or bigger than the dog.")

# arc.py showrunner authority block (Korean). Personality/ability/fear facts.
CHARACTER_FACTS = (
    "## 캐릭터 사실 (권위 — 여기 없는 성격/능력/공포는 발명 금지)\n"
    "⚠️ **종 절대 혼동 금지: 레오 = 고양이(cat, 주황 태비). 랴니 = 개(dog, 프렌치불독). "
    "절대 뒤바꾸지 마라.** 레오를 개로/랴니를 고양이로 쓰면 치명적 오류. "
    "('랴니엄마' = 레오가 랴니(개)를 부르는 애칭, 사람도 고양이도 아님.)\n"
    "- **레오(레오)**: 8개월 **수컷** 고양이(주황 태비). 2025-11-15 떠돌이로 구조됨 → "
    "랴니를 엄마로 여김('랴니엄마'는 레오 POV 호칭). 장난꾸러기·사냥꾼·매복 전문. "
    "**가끔 자기 꼬리를 잡으려고 빙빙 도는 꼬리잡기 놀이를 한다 (PD 2026-06-13, 귀여운 습성 — "
    "에피소드 소재로 활용 가능).** "
    "세차를 무서워함. 고양이라 물을 피하고 물가에서 구경하는 쪽.\n"
    "- **랴니(랴니)**: 11살 **암컷(중성화)** 프렌치불독, 꼬리 없음. "
    "★**꼬리가 없으므로 '꼬리를 흔든다/꼬리를 친다' 류 묘사를 절대 쓰지 마라** — 기쁨/흥분은 "
    "**꼬리 없는 엉덩이(전체)를 좌우로 실룩이는 위글**로 표현한다(프렌치불독 특유). 의젓한 누나/엄마, "
    "차분·현명. ★ **물을 엄청 좋아하는 '물 매니아'**: 물만 보면 흥분해서 짖고, 특히 "
    "**고무호스/분수** 물을 보면 격하게 흥분해 **분수에 뛰어들려고 난리**. **수영도 아주 잘함"
    "('펠프스급')**. 겨울엔 **눈을 좋아하고 얼음 썰매를 탄다**. (거짓 금지: '랴니 물 공포/물 "
    "무서워함'은 완전히 틀림 — 정반대. 단 2016 아기 시절엔 잠깐 무서워했음 → 과거 회상에서만.) "
    "세차도 안 무서워함(레오와 대비). "
    "★ **잠버릇 (PD 2026-06-13, 귀여운 소재 — 에피소드 활용 가능)**: 가끔 **눈을 뜬 채로 깊이 "
    "잔다** — 눈동자가 렘수면처럼 빠르게 움직이고, 팔다리가 파르르 떨리며(꿈꾸는 듯), 눈앞에 손을 "
    "흔들어도 안 보이는 듯 무반응. '풉풉' 하는 숨소리(코골이 비슷)도 낸다.\n"
    "- **여름 물놀이/분수/수영 + 겨울 눈/얼음썰매 컨셉의 주인공 = 랴니.** 레오는 물가 구경/마른 쪽.\n"
    "- **간식·먹거리 (PD 2026-06-12, 사실 — 지어내지 말 것)**: "
    "★레오 = **츄르(고양이 간식 튜브)** 좋아함, **부추**도 먹음. **그릭요거트는 안 먹음.** "
    "★랴니 = **그릭요거트** 잘 먹음, 11살 **노령견이라 관절 영양제**를 챙겨먹음"
    "(츄르처럼 생긴 긴 튜브형 페이스트). "
    "⚠️ **랴니 '바닥 부스러기 주워먹기' 습성 = 카페 한정 (PD 2026-06-12)**: 랴니는 **카페**에서 "
    "테이블 밑에 떨어진 부스러기를 주워먹는 습성이 있다 — 이건 **set이 카페일 때만** 그려라. "
    "그 외 모든 장면(해변/집/야외/판타지/물놀이 등)에선 **바닥에 코를 박지 말고** 랴니의 자세는 "
    "그 컷의 액션을 따른다(바다 입수·앉기·올려다보기 등). (Seedance/이미지 모델이 '바닥 부스러기' "
    "단어만 보면 컨셉과 무관하게 부스러기를 지어내고 고개를 박는 경향 — 카페 외엔 디폴트로 넣지 마라.) "
    "그 외 먹는 장면도 **손에 든 튜브/그릇에서** 먹는 것이지 바닥 핥기가 아니다. "
    "★랴니 **봄 취미 = 산책 나가서 개망초(망초) 어린잎을 뜯어 먹는 것**. "
    "→ '둘이 각자 간식' 컨셉: 레오=츄르/부추, 랴니=그릭요거트/관절영양제튜브. 서로 안 먹는 것 바꿔주지 마라.\n"
    "- **카페 = 랴니의 홈그라운드. 첫 방문이 2015년이라 랴니는 산전수전 다 겪은 '만렙 카페러'다.** "
    "그러니 랴니의 카페 장면을 **'첫 경험/첫 카페/낯선 카페/처음이라 어색해함'으로 절대 쓰지 마라** — "
    "여유·능숙·단골 톤으로 그려라(랴니는 카페에서 제일 편안한 표정). 상대적 신참은 **레오**(2025년~)이니 "
    "'처음/도전' 톤은 레오에게만. 둘의 '공동 첫 카페/첫 경험'은 존재하지 않으니 지어내지 마라.\n"
    "- **랴니의 장기(개인기) 2종 (PD 2026-06-19): '코'와 '브이'** — 캐릭터 이름이 아니라 트릭 "
    "이름이다. ① **'코'** = 사람이 손가락으로 랴니 코를 살짝 콕 하면 랴니가 가만히 받는 것. "
    "② **'브이'** = 사람 손을 ㄴ자(V자)로 받치면 랴니가 그 위에 턱을 척 얹어 기대는 것. 카페·집에서 "
    "자주 보여주는 시그니처. ⚠️ VLM/캡션이 이 장면을 '사람이 쓰다듬는다/코를 만진다'로 밋밋하게 읽기 "
    "쉬운데, 쓰다듬기가 아니라 랴니의 **장기 '코'/'브이'**다 — 손가락이 코에 콕=코, 손에 턱을 얹음=브이로 "
    "살려라(예: '오늘의 장기, 코 콕!', '턱 척, 브이!'). 얼굴 노출 금지, 손만 보인다.\n"
    "- **태풍 = 랴니의 남자친구(친구 개) = 그 동네 산책로의 '노란 진돗개(yellow Jindo)'.** "
    "식별 조건이 명확하다: **산책 장소에서 랴니와 함께 있는 노란 진돗개 = 태풍**. 그러면 '낯선 개/"
    "어떤 개/새 친구'라 하지 말고 **이름 '태풍'**으로 불러라(예: '오랜만에 태풍이랑 산책', "
    "'태풍이도 반가운가 봐요'). 노란 진돗개가 아닌(다른 견종/색) 개는 태풍이라 단정하지 마라.\n"
    "- **남산이 = 랴니의 친구(친구 개) = 흰 몸통에 갈색 귀·얼굴 무늬가 있는 작은 잭러셀계 개.** "
    "식별 조건: **실내(집)에서 랴니와 함께 있는, 갈색 귀를 가진 흰 소형견 = 남산이.** ★둘은 "
    "**여러 번 만난 사이 — '첫 만남/낯선 친구/처음 인사'가 절대 아니다**(랴니가 남산이네 집에 "
    "놀러 가곤 한다). 이 개를 '하얀 친구/낯선 개/첫 만남'이라 쓰지 말고 **이름 '남산이'**로 부르고 "
    "**이미 친한 사이**로 그려라(예: '오랜만에 남산이네 놀러 간 날', '반가워 왕왕 짖는 남산이'). "
    "남산이의 귀여운 포인트 = **반가우면 왕왕 짖으면서도 빈백 같은 높은 자리(명당)부터 차지**하는 것. "
    "흰색이라도 잭러셀계가 아니거나 야외 산책로의 개는 남산이라 단정하지 마라.\n"
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
    "WHITE BLAZE that must be a THIN NARROW line (NOT a wide splash) from nose to "
    "forehead — **flag a THICK or WIDE center blaze as a defect** (it should read as a "
    "fine pencil-width line); a FAINT subtle eyebrow-like white mark above each eye "
    "(small/thin, present but understated — NOT a bold round dot), "
    "silver-grey aged muzzle, white chin, large white chest patch, "
    "large UPRIGHT bat ears (erect/pointed up — NOT folded rose ears; flag folded "
    "ears as a defect), ABSOLUTELY NO TAIL (her rear is bare — flag any tail rendering as "
    "a major failure), petite refined feminine build (NOT a muscular barrel-chested "
    "male), only black/white/grey — no brown. Her BACK / neck / spine are SOLID BLACK "
    "— flag any white stripe or line down the back or neck as a failure (the white "
    "blaze is FOREHEAD-only). ALSO flag as a MAJOR failure any "
    "distorted / melted / uncanny face, mismatched or asymmetric eyes, or a floating "
    "white blob/orb artifact on the face — these are common when a still photo is "
    "animated (photo_i2v) and MUST lower the verdict, not pass. "
    "RELATIVE SIZE: she is a stocky, heavy adult French Bulldog and reads BIGGER and "
    "heavier than Leo the cat — in any AI-rendered cut sharing the frame with Leo, flag "
    "as a defect any frame where Leo appears as large as or larger than Ryani (Seedance "
    "tends to over-size the cat). Real-clip cuts are exempt — there the sizes are real, "
    "and camera perspective can make either pet look big; judge size only on AI cuts.")
REVIEW_LEO = (
    "**Leo (orange tabby, ~8mo, MALE)**: pale yellow-green chartreuse eyes (NOT "
    "gold-amber), faint scar across nose bridge, white chin tuft. Tail often in a "
    "question-mark shape. Lean agile body, paler cream-orange cheeks/belly than the back. "
    "RELATIVE SIZE: he is a SMALL, slim young cat — in any AI-rendered cut he must read "
    "NOTICEABLY SMALLER and lighter than the stocky adult Frenchie Ryani; flag Leo "
    "rendered large, chunky, or bigger than the dog as a defect (real-clip cuts exempt).")


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
