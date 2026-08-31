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
    "blaze": "THIN narrow white blaze (a fine pencil-width line)",  # a fine line, NOT a wide splash
    "ears": "large UPRIGHT bat ears (erect, pointed up — NOT folded rose ears)",  # PD 2026-06-10
    "muzzle": "short brachycephalic muzzle with characteristic wrinkles (nose-rope fold over the nose bridge + cheek/brow folds) — NOT a smooth snout",  # PD 2026-07-01
    "exists_from": "2015-05-05",     # born — cannot appear in footage before this
}
LEO = {
    "name_ko": "레오",
    "species_en": "orange tabby cat",
    "sex_en": "MALE",                # he/him
    "sex_ko": "수컷",
    "age_months": 10,                # grown young-adult now (~Ryani-sized). NEVER "veteran"/senior/11년차
    "eyes_en": "pale yellow-green / chartreuse",  # NOT gold, NOT amber
    # Born ~2025-09-25, rescued 2025-11-15. He CANNOT appear in any earlier footage —
    # an orange cat in pre-2025 clips is a different/stray cat, NOT Leo. (PD 2026-06-22:
    # a 2020 clip got captioned "5년 전 레오"; this date is the machine-usable boundary
    # the VLM tagger + subject guard read so that can't happen again.)
    "exists_from": "2025-09-25",
}

# ── Pre-Leo cat canon (PD 2026-08-01) ─────────────────────────────────
# Before Leo was rescued (2025-09), the cat in Ryani's life was 원두(Wondu) — the
# resident cat at Ryani's favourite café, whom she adored. 원두 has since crossed the
# rainbow bridge. This is why a grey/tabby cat in PRE-2025-09 footage is NOT Leo (orange
# tabby, born 2025-09): it is 원두. The RF caption pipeline reads only the frame and is
# told "name the cat 레오" — so a café-cat clip from 2024 got captioned "레오", a factual
# lie PD caught twice (카페편 "2024 신참 레오" → RF0800 "1.6년 전 레오"). Naming the
# pre-Leo cat 원두 turns the boundary into a warm story instead of a hole. The tone is
# fond/memorial (like 삐용이) — never morbid.
WONDU = {
    "name_ko": "원두",
    "who": "레오를 만나기 전, 랴니가 가장 좋아하던 카페에 살던 고양이 (지금은 무지개다리를 건넘)",
    "tone": "그립고 다정하게 — 슬픔을 전시하지 말 것",
}
# What a temporally-impossible pet name should become in a caption (deterministic backstop).
# The cat before Leo is 원두; a pre-2015 'dog' (essentially never happens) falls back generic.
PRELEO_CAT_NAME = WONDU["name_ko"]     # 원두


def pet_exists_on(pet: str, captured_iso: "str | None") -> bool:
    """Could `pet` (canonical key 'ryani'/'leo') appear in footage captured at
    captured_iso? Missing/blank date or unknown pet → True (never strip on no data).
    The single source for temporal subject grounding — VLM tagger, the producer RF
    subject guard, and any caption check all read this, so the boundary is defined once."""
    if not captured_iso:
        return True
    ef = {"ryani": RYANI, "leo": LEO}.get((pet or "").strip().lower(), {}).get("exists_from")
    return True if not ef else str(captured_iso)[:10] >= ef


def age_era_at(pet: str, captured_iso: str) -> str:
    """The pet's life-era when `captured_iso` was filmed, as an ENDEARING caption hook —
    "아기" (baby) / "어린" (young) / "" (adult, no special label). Derived from the canon
    birth date (exists_from). PD 2026-06-30: a memory-lane opener must lead with the pet's
    young era ("반년 전, 아기 레오!") — the youth is the hook — not generic season/weather.
    Thresholds differ by species (a cat matures faster): cat 아기<6mo·어린<12mo;
    dog 아기<8mo·어린<18mo. Returns "" on missing data (never invent a baby label)."""
    if not pet or not captured_iso:
        return ""
    key = pet.strip().lower()
    rec = {"ryani": RYANI, "leo": LEO}.get(key)
    if not rec or not rec.get("exists_from"):
        return ""
    try:
        import datetime as _dt
        born = _dt.date.fromisoformat(rec["exists_from"][:10])
        shot = _dt.date.fromisoformat(str(captured_iso)[:10])
        months = (shot - born).days / 30.44
    except Exception:
        return ""
    if months < 0:
        return ""
    is_cat = key == "leo"
    baby_max, young_max = (6, 12) if is_cat else (8, 18)
    if months < baby_max:
        return "아기"
    if months < young_max:
        return "어린"
    return ""


# ── Deterministic canon age/year corrector ────────────────────────────
# The single home for "the Writer invented a wrong age/birth-year" fixes. A prompt
# rule alone gets rubber-stamped by Giri, so we correct it deterministically at every
# viewer-facing chokepoint (caption burn + upload title/description, both lanes). The
# channel has exactly two birth years and one age gap, all derived from the canon dates
# above — so any other value in an age-GAP or "N년생"/"born YYYY" phrasing is a
# hallucination. Precision over recall: only age-gap and birth-year phrasings are
# touched, so legitimate numbers (event years like "2016년 첫 수영", counts) never break.
import re as _re_canon

_RYANI_BIRTH_YEAR = int(RYANI["exists_from"][:4])   # 2015
_LEO_BIRTH_YEAR = int(LEO["exists_from"][:4])        # 2025
_CANON_BIRTH_YEARS = tuple(sorted({_RYANI_BIRTH_YEAR, _LEO_BIRTH_YEAR}))
_AGE_GAP_YEARS = abs(_LEO_BIRTH_YEAR - _RYANI_BIRTH_YEAR)  # 10

_AGE_GAP_KO = _re_canon.compile(r"\d+\s*살\s*차이")
_AGE_GAP_EN = _re_canon.compile(
    r"\b\d+[\s-]*year[s]?([\s-]+(?:gap|difference|apart))", _re_canon.IGNORECASE)
_BIRTH_YEAR_KO = _re_canon.compile(r"(\d{4})\s*년\s*생")
_BIRTH_YEAR_EN = _re_canon.compile(r"\b([Bb]orn)\s+(\d{4})\b")


def _snap_birth_year(y: int) -> int:
    return min(_CANON_BIRTH_YEARS, key=lambda c: (abs(y - c), c))  # tie → older


# PD 2026-07-23: the EN caption sometimes romanizes 랴니 as a NON-canonical spelling
# ("Lani" — 07-25 21:00 RF cut2 wrote "Lani, professional Leo-meal observer" for KO 랴니).
# The canonical romanizations are FIXED: 랴니 = "Ryani" (the dog), 레오 = "Leo" (the cat). A
# wrong spelling reads to a viewer as a name error / a different character, and the reviewer
# bounced the episode over it (caption-vs-clip "name error"). Snap known misromanizations of
# Ryani back to canon deterministically — the Writer/LLM drifts, the corrector doesn't.
_CANON_NAME_FIX = (
    (_re_canon.compile(r"\b(?:Lani|Lanni|Riani|Ryanie|Ryany|Ryanni|Rhyani|Rhyanni|Rihani|Ryanee)\b",
                       _re_canon.IGNORECASE), "Ryani"),
    # PD 2026-08-22 (21:00 RF): the KO caption named the dog "랑이" ("랑이 언니한테 인사…") —
    # a misspelling of 랴니 that reads as a different pet. Snap it to canon like the EN
    # misromanizations above. Guarded by a Hangul-lookbehind so it never eats a legitimate
    # word that merely ENDS in "랑이" (e.g. 사랑이 = "love"+subject marker).
    (_re_canon.compile(r"(?<![가-힣])랑이"), "랴니"),
    # PD 2026-08-27 (8/28 RF): the KO caption also drops the ㅑ and writes the dog as "라니"
    # ("라니도 합류 / 라니는 포근하게") — another misspelling of 랴니. Snap it, but tightly: only
    # when "라니" is the NAME (followed by a subject/object/topic particle or space), NEVER the
    # common quotative ending "-라니까/-라니요/-라니" (먹으라니까). The Hangul-lookbehind already
    # excludes verb-attached quotatives (preceded by a syllable); the lookahead is the backstop.
    (_re_canon.compile(r"(?<![가-힣])라니(?=[는가를도와의야랑이\s]|한테|$)"), "랴니"),
    # …and Leo is the 막내: his sibling term for the dog is 누나 (or the nickname 랴니엄마),
    # never 언니 (a female speaker's word). "랴니 언니" is an unambiguous canon slip in this
    # two-pet cast — no one here would call Ryani 언니 — so snap it after the name fix above.
    (_re_canon.compile(r"랴니\s*언니"), "랴니 누나"),
)


def correct_canon_names_text(text: str) -> str:
    """Snap non-canonical pet-name romanizations in viewer-facing text to canon
    (랴니 → 'Ryani'). Idempotent on already-canonical text ('Ryani'/'Leo' are untouched).
    Folded into correct_canon_age_text so it runs at every canon-text chokepoint."""
    if not text:
        return text
    for rx, canon_name in _CANON_NAME_FIX:
        text = rx.sub(canon_name, text)
    return text


def correct_canon_age_text(text: str) -> str:
    """Force the pets' age-gap, birth-year, AND name romanization to canon in viewer-facing
    text.

    Ryani 2015 / Leo 2025 → gap is always 10y; a "N살 차이"/"N-year gap" other than that,
    or a "N년생"/"born YYYY" that isn't 2015/2025, is a fabricated canon violation. A non-
    canonical name spelling (e.g. "Lani" for Ryani) is likewise snapped to canon. Idempotent
    on already-correct text. Call at each output chokepoint (burn captions, upload title)."""
    if not text:
        return text
    if "살 차이" in text:
        text = _AGE_GAP_KO.sub(f"{_AGE_GAP_YEARS}살 차이", text)
    text = _AGE_GAP_EN.sub(lambda m: f"{_AGE_GAP_YEARS}-year" + m.group(1), text)
    if "년생" in text:
        text = _BIRTH_YEAR_KO.sub(lambda m: f"{_snap_birth_year(int(m.group(1)))}년생", text)
    text = _BIRTH_YEAR_EN.sub(lambda m: f"{m.group(1)} {_snap_birth_year(int(m.group(2)))}", text)
    text = correct_canon_names_text(text)
    text = _scrub_geuhae(text)
    return text


# ── "그해" memory-lane phrase ban (PD 2026-08-15, re-flagged 2026-08-19) ───────────────
# A past⇄present memory-lane caption must not say "그해 (가을/겨울/…)" ("that year/autumn"). PD wants
# a plain "몇 년 전". The caption_agent.md prompt ban kept leaking (a burned "그해 가을" shipped on
# 8/19 RF1800), so enforce it deterministically at the burn/upload chokepoint. "그해 가을" → "몇 년
# 전 가을"; a bare "그해"/"그 해" → "몇 년 전". Idempotent (the result doesn't re-match).
_GEUHAE_KO = _re_canon.compile(r"그\s?해(\s*(?:봄|여름|가을|겨울))?")


def _scrub_geuhae(text: str) -> str:
    if not text or "그" not in text:
        return text
    return _GEUHAE_KO.sub(lambda m: "몇 년 전" + (m.group(1) or ""), text)


# ── Capture-date-aware memory-lane timeframe (PD 2026-08-31) ──────────────────
# A memory-lane caption must state HOW LONG AGO from the clip's REAL capture date — not a
# blanket "몇 년 전". Leo came to the house 2025-09, so every baby-Leo clip is < ~1 year old;
# calling it "몇 년 전" (years ago) is a lie — it's "지난 가을/겨울" or "몇 개월 전". The dateless
# _scrub_geuhae hardcodes "몇 년 전" and over-claims for recent footage, so when the clip's
# capture date IS known, compute the phrase from elapsed months instead. The same math still
# reads "몇 년 전" for genuinely old footage (Ryani's 2016 clips), so it's one rule for both
# pets: trust the date, not a fixed string. The VLM captioner is guided to WRITE the right
# framing from the date; this is the deterministic backstop that fixes any "년" over-claim.
_SEASON_KO = {12: "겨울", 1: "겨울", 2: "겨울", 3: "겨울", 4: "봄", 5: "봄",
              6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}
_YEARS_AGO_KO = _re_canon.compile(r"(?:몇|여러|\d+)\s*년\s*전")


def _elapsed_months(captured_iso: "str | None", now=None):
    import datetime as _dt
    try:
        shot = _dt.date.fromisoformat(str(captured_iso)[:10])
    except Exception:
        return None
    return ((now or _dt.date.today()) - shot).days / 30.44


def timeframe_phrase(captured_iso: "str | None", now=None) -> str:
    """Canonical '얼마 전' phrase for a memory-lane caption, from the clip's capture date.
    < ~14mo → '지난 <계절>' (last autumn/winter…); 14–30mo → '작년 <계절>'; ≥30mo → 'N년 전'.
    '' on missing/future date (never invent a timeframe). Feed as a WRITING hint to the VLM."""
    import datetime as _dt
    m = _elapsed_months(captured_iso, now)
    if m is None or m < 0:
        return ""
    season = _SEASON_KO.get(_dt.date.fromisoformat(str(captured_iso)[:10]).month, "")
    if m < 3:
        return "얼마 전"
    if m < 14:
        return f"지난 {season}" if season else "몇 개월 전"
    if m < 30:
        return f"작년 {season}" if season else "1년 전"
    return f"{round(m / 12)}년 전"


def correct_timeframe_text(text: str, captured_iso: "str | None") -> str:
    """Snap an over-claimed memory-lane timeframe in KO caption text to the clip's real age.
    On recent footage (< ~14mo) a "몇 년 전 / N년 전 / 그해" becomes "지난 <계절>" (or "몇 개월 전"
    if the season is unclear); on genuinely old footage it's left as "몇 년 전". No/undated
    clip → falls back to the dateless _scrub_geuhae. Idempotent. Call at the burn chokepoint
    with the cut's capture date (KO only — the timeframe idioms are Korean)."""
    if not text:
        return text
    import datetime as _dt
    m = _elapsed_months(captured_iso)
    if m is None or m < 0:
        return _scrub_geuhae(text)
    season = _SEASON_KO.get(_dt.date.fromisoformat(str(captured_iso)[:10]).month, "")
    recent = m < 14
    ago = (f"지난 {season}" if season else "몇 개월 전") if recent else "몇 년 전"
    if "그" in text:
        text = _GEUHAE_KO.sub(
            lambda mm: (f"지난 {mm.group(1).strip()}" if (recent and mm.group(1))
                        else f"몇 년 전{mm.group(1) or ''}" if mm.group(1) else ago), text)
    if recent:
        text = _YEARS_AGO_KO.sub(ago, text)
    return text


# ── Temporally-impossible pet-name corrector (PD 2026-08-01, RF0800) ───
# A caption over a clip that PREDATES a pet's existence must not name that pet. The RF
# caption VLM reads only the frame and is told to name our pets — so a café cat in a 2024
# clip (before Leo's 2025-09 birth) got captioned "레오". This is the deterministic
# backstop the frame-blind VLM stages lack: given a caption + the clip's capture date,
# swap an impossible pet name for the right one (the pre-Leo cat is 원두; a pre-2015 dog is
# generic 강아지). pet_exists_on is the single boundary. Idempotent on already-correct text.
_LEO_TOKEN_KO = _re_canon.compile(r"레오")
_LEO_TOKEN_EN = _re_canon.compile(r"\bLeo\b")
_RYANI_TOKEN_KO = _re_canon.compile(r"랴니")
_RYANI_TOKEN_EN = _re_canon.compile(r"\bRyani\b")


def correct_preleo_pet_names_text(text: str, captured_iso: "str | None") -> str:
    """If `captured_iso` predates a pet's existence, neutralize that pet's name in `text`.
    레오/Leo → 고양이/the cat; 랴니/Ryani → 강아지/the dog (pre-2015, essentially never).
    No date, or a date within existence → unchanged. Deterministic backstop — call at the
    burn chokepoint after any VLM caption rewrite so an impossible name can't survive.

    NOTE (PD 2026-08-01): the safe default is GENERIC (고양이), NOT 원두. One pre-Leo cat is
    원두 (the café cat, see canon.WONDU) but NOT every pre-Leo cat is — don't over-attribute.
    When PD/context confirms a clip IS 원두, name it 원두 explicitly in the caption (that name
    exists, so this backstop leaves it untouched); this function only strips the impossible
    '레오'."""
    if not text or not captured_iso:
        return text
    if not pet_exists_on("leo", captured_iso):
        text = _LEO_TOKEN_KO.sub("고양이", text)
        text = _LEO_TOKEN_EN.sub("the cat", text)
    if not pet_exists_on("ryani", captured_iso):
        text = _RYANI_TOKEN_KO.sub("강아지", text)
        text = _RYANI_TOKEN_EN.sub("the dog", text)
    return text

# ──────────────────────────────────────────────────────────────────────
# Rendered blocks — authoritative text. Edit HERE; consumers import these.
# ──────────────────────────────────────────────────────────────────────

# cameraman.py per-cut Seedance marking injection (note the leading space —
# it is appended to a motion_prompt).
RYANI_MARKING = (
    " CRITICAL — Ryani the black French Bulldog must keep her exact markings every "
    "frame: a THIN narrow white blaze (a fine pencil-width line up the muzzle, between "
    "the eyes, to the forehead — NOT a wide splash, do NOT enlarge it) from nose up the "
    "forehead, silver-grey aged muzzle. She is a BRACHYCEPHALIC French Bulldog: keep her "
    "short flat muzzle with its CHARACTERISTIC WRINKLES — soft folds of skin across the "
    "nose bridge and above the black nose (the 'nose rope') plus gentle cheek/brow folds. "
    "Her snout is NOT smooth or featureless; those muzzle wrinkles are part of her face and "
    "must read in every close shot. "
    "white chin, white chest patch, large UPRIGHT "
    "bat ears (erect/pointed up, NOT folded rose ears), NO "
    "tail. ★NO TAIL IN ANY POSE — her rear is completely bare and tailless even when her "
    "rump is RAISED (play-bow, butt-up), when she TURNS AWAY, or is seen FROM BEHIND; do NOT "
    "sprout a tail, tail-nub or stub on a raised/rear-facing rump (Seedance tends to add one "
    "there — that is WRONG). Her joy is a tailless BUTT-WIGGLE, never a wagging tail. "
    "Her white is ONLY: the forehead blaze, chin, the FRONT of the throat (the "
    "chin-white flows down the FRONT of the neck into the chest patch — that "
    "front-of-throat white is CORRECT, keep it), and toes. Her BACK, the NAPE (back "
    "of the neck / behind the head) and spine are SOLID BLACK — NO white spot, dot, "
    "patch, stripe or line on the back, nape or spine (Seedance/i2v often hallucinates "
    "a white dot/patch on the BACK of the neck — that is WRONG; the nape is pure black). "
    "Ryani is a FRENCH BULLDOG, NOT a Boston Terrier — she has NONE of a Boston "
    "Terrier's tuxedo white creeping up the neck/back; her nape, spine and back are "
    "solid black. The only character with that tuxedo white is 삐용이 (her late friend); "
    "any white creeping onto Ryani's neck or back is 삐용이's marking bleeding over and "
    "is WRONG. "
    "Only black/white/grey — no brown. Above each eye she has a FAINT, subtle "
    "eyebrow-like white mark (small and thin, brow-like — present but understated; "
    "NOT a bold or large round dot). "
    "SIZE: she is a stocky, solid adult French Bulldog. Leo has now grown into a young-"
    "adult cat of roughly HER size, so in a present-day two-shot the two read COMPARABLE — "
    "she may look a touch heavier/broader in build, but NOT dramatically larger. Render "
    "Leo as a full-grown cat beside her, never a tiny kitten (a kitten-sized Leo is correct "
    "ONLY in an explicit baby/past-era memory-lane cut). "
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
    " SIZE: Leo has grown into a young-adult cat (~10 months) roughly the SAME size as "
    "Ryani — in a present-day two-shot render him COMPARABLE in size to the small French "
    "Bulldog, a full-grown cat, NOT a tiny kitten (the wink-cut failure was a baby-sized "
    "Leo dwarfed by adult Ryani). He stays lean and agile against her stocky build, so she "
    "may read a touch broader, but he is NOT dramatically smaller. A kitten-small Leo is "
    "correct ONLY when the cut is explicitly his baby/past era.")

# generate_character_scene.py image-generation identity canon.
RYANI_IMAGE_CANON = (
    "Ryani — REAL black French Bulldog, SPAYED FEMALE (she/her, 11-year-old "
    "senior; clearly female, NO male anatomy). Markings (keep EXACTLY, do not "
    "redraw): a THIN NARROW white blaze (a fine pencil-width line from nose up "
    "the forehead — NOT a wide splash, do NOT enlarge it), silver-grey aged muzzle. "
    "She is BRACHYCEPHALIC: keep her short flat French-Bulldog muzzle with its "
    "characteristic WRINKLES — soft skin folds across the nose bridge and above the black "
    "nose (the 'nose rope') plus gentle cheek/brow folds; her snout is NOT smooth or "
    "featureless. "
    "white chin, white chest patch, white toes, a FAINT subtle eyebrow-like white mark "
    "above each eye (small/thin, brow-like — NOT a bold round dot), "
    "large UPRIGHT bat ears (erect/pointed up, NOT folded rose ears), ABSOLUTELY NO "
    "TAIL. White appears ONLY on the forehead blaze, chin, the FRONT of the throat (the "
    "chin-white runs down the FRONT of the neck into the chest patch — keep that), and "
    "toes. Her BACK, the NAPE (back of the neck / behind the head) and spine are SOLID "
    "BLACK — NO white spot, dot, patch, stripe or line on the back, nape or spine (a "
    "hallucinated white dot/patch on the BACK of the neck is WRONG — the nape is pure black). "
    "Only black/white/grey — NO brown. Petite, "
    "refined, feminine build (NOT a muscular barrel-chested male) — but still a stocky, "
    "solid adult French Bulldog: Leo is now a young-adult cat of comparable size, so in a "
    "present-day two-shot the two read SIMILAR — she may be a touch heavier in build but "
    "NOT dramatically larger, and Leo is never rendered kitten-tiny beside her (except in "
    "an explicit baby-era cut). A REAL dog, not a cartoon. POSE = MATCH THE SCENE'S ACTION (PD 2026-06-12): render exactly the "
    "action this cut describes (splashing in waves, sitting, looking up, leaping, "
    "being held, etc.). Do NOT auto-insert a nose-down sniffing/licking-the-floor "
    "pose when the action is something else — floor-sniffing is correct ONLY when "
    "this scene's action actually calls for it; otherwise keep her head/face up.")

LEO_IMAGE_CANON = (
    "Leo — REAL orange tabby cat, MALE (he/him, young ~10 months). Pale "
    "YELLOW-GREEN / chartreuse eyes (NOT amber, NOT gold), white chin tuft, white "
    "whiskers, lean agile young-adult body, paler cream-orange cheeks and belly "
    "than the back. A REAL cat, not a cartoon. SIZE: a young-adult cat now grown to roughly "
    "Ryani's size — in a present-day two-shot render him COMPARABLE in size to the small "
    "French Bulldog, a full-grown lean cat, NOT a tiny kitten. He is leaner than her stocky "
    "build (so she can read a touch broader), but not dramatically smaller. Kitten-small is "
    "correct ONLY in an explicit baby/past-era cut.")

# arc.py showrunner authority block (Korean). Personality/ability/fear facts.
CHARACTER_FACTS = (
    "## 캐릭터 사실 (권위 — 여기 없는 성격/능력/공포는 발명 금지)\n"
    "⚠️ **종 절대 혼동 금지: 레오 = 고양이(cat, 주황 태비). 랴니 = 개(dog, 프렌치불독). "
    "절대 뒤바꾸지 마라.** 레오를 개로/랴니를 고양이로 쓰면 치명적 오류. "
    "('랴니엄마' = 레오가 랴니(개)를 부르는 애칭, 사람도 고양이도 아님.)\n"
    "- **레오(레오)**: 10개월 **수컷** 고양이(주황 태비). 2025-11-15 떠돌이로 구조됨 → "
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
    "무서워함'은 완전히 틀림 — 정반대. 단 2016 아기 시절엔 잠깐 어색해/서툴렀음 → 과거 회상에서만.) "
    "세차도 안 무서워함(레오와 대비). "
    "★**수영/물놀이는 랴니의 평생 특기 — '첫 수영/처음 물/물 도전' 톤 금지**(카페 가드와 같은 이유). "
    "옛 2016 클립이라도 '인생 첫 풍덩/첫 수영장' 식으로 단정하지 마라(그 시절도 무서워한 게 아니라 잠깐 "
    "서툴렀을 뿐). 맞는 앵글 = **'처음엔 서툴렀는데 이젠 완벽 적응 / 물에서 안 나오려 함 / 펠프스급 물개'** "
    "(서툴렀던 아기 → 지금은 완벽 progression). 물에서 '첫 도전/신참' 톤은 물을 피하는 **레오**에게만. "
    "★ **잠버릇 (PD 2026-06-13, 귀여운 소재 — 에피소드 활용 가능)**: 가끔 **눈을 뜬 채로 깊이 "
    "잔다** — 눈동자가 렘수면처럼 빠르게 움직이고, 팔다리가 파르르 떨리며(꿈꾸는 듯), 눈앞에 손을 "
    "흔들어도 안 보이는 듯 무반응. '풉풉' 하는 숨소리(코골이 비슷)도 낸다.\n"
    "- **여름 물놀이/분수/수영 + 겨울 눈/얼음썰매 컨셉의 주인공 = 랴니.** 레오는 물가 구경/마른 쪽.\n"
    "- **랴니 생리(월경) 기간 (PD 2026-08-26, 사실)**: 강아지용 **기저귀**를 채우고, 흘러내리지 않게 "
    "**멜빵(하네스형 서스펜더)**을 함께 착용한다. 이 시기엔 **나른하고 얌전**하다('꽃도장 찍는 중'이라 표현). "
    "⚠️ **이 기저귀+멜빵을 '넥카라(넥칼라/보호대)'나 '옷/패션'으로 오독하지 마라** — 생리대 착용이다. "
    "해당 footage의 캡션은 이 맥락(생리 기저귀·나른한 랴니)을 살려 쓰고, 넥카라/아파서 등으로 지어내지 마라.\n"
    "- **간식·먹거리 (PD 2026-06-12, 사실 — 지어내지 말 것)**: "
    "★레오 = **츄르** 좋아함, **부추**도 먹음. **그릭요거트는 안 먹음.** "
    "(⚠️ **부추/캣그라스 등 잎채소 간식을 AV 화면의 히어로 소품으로 테이블에 올리지 마라** — "
    "Seedance가 유리/가구 표면에서 **자라나는 풀·잔디**로 렌더한다(8/25 간식HQ에서 유리식탁에 풀이 "
    "자라나 캡션까지 '캣그라스가 더 좋아'로 합리화됨). 먹는다는 사실은 캡션으로만 언급하고, 화면 소품은 "
    "청어·츄르·그릭요거트·치즈 같은 **접시/튜브류**로 — 이들은 깨끗하게 렌더된다.) "
    "(★츄르 모양 = 손으로 짜서 주는 **가느다란 스틱형 파우치**(작은 막대 비닐포, 손가락 두께). "
    "굵은 **치약튜브처럼 그리지 마라** — Seedance가 자주 치약 튜브로 키운다. 그릇에 짜놓은 흰 페이스트도 아님.) "
    "★랴니 = **그릭요거트** 잘 먹음, 11살 **노령견이라 관절 영양제**를 챙겨먹음"
    "(츄르처럼 생긴 긴 튜브형 페이스트). "
    "★**말린 간식**: **청어(새끼) 말린 것과 소고기(쇠고기) 말린 것 = 레오·랴니 둘 다 잘 먹는 공동 "
    "간식**이다(둘 중 하나만의 것이 아니다). **화면에 둘 다 먹고 있으면 캡션도 둘 다 먹는 것으로 써라** — "
    "한 마리가 '싹쓸이/독차지'했다는 식으로 한쪽만 크레딧하지 마라(둘이 나란히 먹는 공동 간식 장면의 "
    "핵심은 '같이 먹는 것'이다). **이 말린 청어의 출처 = 함미(할머니)가 시장(장)에서 직접 사온 것**이다 — "
    "하비(할아버지)가 집에서 말리는 게 아니고 '하비 간식'도 아니다(간식을 만든/주는 주체를 하비로 돌리지 "
    "마라; 출처를 언급해야 하면 '함미가 장에서 사온 말린 청어'). 그 외 레오는 **민물새우 말린 것**도, 랴니는 **치즈**도 먹는다. "
    "⚠️ **바닥이나 그릇에 놓인 작은 마른 생선은 '멸치'가 아니라 '청어(새끼) 말린 간식'이다** — "
    "이 집 마른 생선 간식은 청어이니, 캡션/VLM이 눈에 보이는 대로 일반명사 '멸치'라고 기본값 처리하지 "
    "않게 하라(생선 종류를 확신 못 하면 '간식'/'말린 간식'으로 두되 절대 '멸치'라 부르지 마라). "
    "⚠️ **랴니 '바닥 부스러기 주워먹기' 습성 = 카페 한정 (PD 2026-06-12)**: 랴니는 **카페**에서 "
    "테이블 밑에 떨어진 부스러기를 주워먹는 습성이 있다 — 이건 **set이 카페일 때만** 그려라. "
    "그 외 모든 장면(해변/집/야외/판타지/물놀이 등)에선 **바닥에 코를 박지 말고** 랴니의 자세는 "
    "그 컷의 액션을 따른다(바다 입수·앉기·올려다보기 등). (Seedance/이미지 모델이 '바닥 부스러기' "
    "단어만 보면 컨셉과 무관하게 부스러기를 지어내고 고개를 박는 경향 — 카페 외엔 디폴트로 넣지 마라.) "
    "그 외 먹는 장면도 **손에 든 튜브/그릇에서** 먹는 것이지 바닥 핥기가 아니다. "
    "★랴니 **봄 취미 = 산책 나가서 개망초(망초) 어린잎을 뜯어 먹는 것**. "
    "→ '둘이 각자 간식' 컨셉: 레오=츄르/부추, 랴니=그릭요거트/관절영양제튜브(서로 안 먹는 것 바꿔주지 마라). "
    "'둘이 같이 먹는 간식' 컨셉이면 = **청어·소고기 말린 것**(공동 간식).\n"
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
    "- **삐용이 = 랴니의 어릴 적 단짝(친구 개) = 흑백 턱시도 무늬의 보스턴테리어**(넓은 흰 블레이즈 + "
    "흰 가슴·다리, 박쥐귀, ★**꼬리 있음**, 랴니보다 날렵). **지금은 무지개다리를 건넌(돌아가신) 친구**다. "
    "옛 외출 영상(특히 **2016년**)에 랴니와 함께 자주 등장한다. 식별: **랴니 곁/근처에 있는 "
    "꼬리 달린 흑백 턱시도 개 = 삐용이지 랴니가 아니다**(랴니는 거의 검정·**꼬리 없음**·턱/가슴만 "
    "가는 흰색인데, 삐용이는 흰색이 훨씬 넓고 꼬리가 있다). ⚠️ **랴니는 보스턴테리어가 아니다**(프렌치불독) "
    "— 옛 영상에 **꼬리 달린 흑백 턱시도 개**가 보이면 삐용이로 분리해 불러라(예: '어릴 적 단짝 삐용이랑'). "
    "★**랴니 목/등에 흰색이 보이면 삐용이의 턱시도 흰색이 번진 것이라 잘못이다** — 랴니 목뒤·척추·등은 "
    "순검정이다. ⚠️ **추모 민감(PD 깊이 "
    "아끼는 친구)**: 삐용이가 나오는 컷은 톤을 **PD와 먼저 확인**하고, 상실을 가벼운 개그로 쓰지 "
    "마라 — 함께 뛰놀던 추억을 담담히, 사실만(날조 금지).\n"
    "- **'하비' = 할아버지, '함미' = 할머니** — 랴니·레오를 키우는 충주 할아버지·할머니 본인을 "
    "부르는 애칭이다. 가족(특히 grandmompapa 채널)이 '하비'라 하면 **사람 할아버지**, '함미'면 "
    "**할머니**다. 펫이나 '아이들/친구들'로 절대 뭉뚱그리지 말고, 캡션·스토리에서도 사람 어르신으로 "
    "다뤄라(예: '하비를 기다리는 랴니와 레오'=할아버지를 기다림, '하비가 못살게 한다'=할아버지가 "
    "장난친다). 얼굴은 다른 사람들처럼 노출하지 않는다.\n"
    "- ⚠️ 위 목록에 없는 공포·능력·트레잇을 새로 지어내지 마라. 나이도 정확히(레오 10개월/"
    "랴니 11살) — 뒤바꾸지 마라.\n"
)

# Universal pet-rendering guardrails (injected into image/video prompts).
GUARD_NO_CLOTHING = (
    "Pets are bare-furred — NO clothing/hanbok/costumes (unless the scene "
    "explicitly specifies a harness or a sanctioned episode costume).")
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
    "**Ryani (French Bulldog, 11yr, SPAYED FEMALE)**: a THIN narrow "
    "WHITE BLAZE that must be a THIN NARROW line (a fine pencil-width line, NOT a wide splash) from nose to "
    "forehead — **flag a THICK or WIDE center blaze as a defect** (it should read as a "
    "fine pencil-width line); a FAINT subtle eyebrow-like white mark above each eye "
    "(small/thin, present but understated — NOT a bold round dot), "
    "silver-grey aged muzzle, white chin, large white chest patch, "
    "large UPRIGHT bat ears (erect/pointed up — NOT folded rose ears; flag folded "
    "ears as a defect), ABSOLUTELY NO TAIL (her rear is bare — flag any tail rendering as "
    "a major failure), petite refined feminine build (NOT a muscular barrel-chested "
    "male), only black/white/grey — no brown. Her BACK, the NAPE (back of the neck) and "
    "spine are SOLID BLACK — flag any white spot, dot, patch, stripe or line on the back, "
    "nape, or BACK of the neck as a failure (white belongs ONLY to the forehead blaze, "
    "chin, the FRONT of the throat/chest patch, and toes — the FRONT-of-throat white is "
    "correct and must NOT be flagged). ALSO flag as a MAJOR failure any "
    "distorted / melted / uncanny face, mismatched or asymmetric eyes, or a floating "
    "white blob/orb artifact on the face — these are common when a still photo is "
    "animated (photo_i2v) and MUST lower the verdict, not pass. "
    "RELATIVE SIZE: Leo has grown to roughly Ryani's size, so in a PRESENT-day AI cut the "
    "two should read COMPARABLE — flag a defect when Leo is rendered as a tiny KITTEN "
    "dwarfed by adult Ryani (baby-Leo drift), or grossly larger than her; comparable sizes "
    "are CORRECT, not a defect. In an explicit baby/past-era cut Leo SHOULD read noticeably "
    "smaller. Real-clip cuts are exempt — there the sizes are real, and camera perspective "
    "can make either pet look big; judge size only on AI cuts.")
REVIEW_LEO = (
    "**Leo (orange tabby, ~10mo, MALE)**: pale yellow-green chartreuse eyes (NOT "
    "gold-amber), faint scar across nose bridge, white chin tuft. Tail often in a "
    "question-mark shape. Lean agile body, paler cream-orange cheeks/belly than the back. "
    "RELATIVE SIZE: now a young-adult cat grown to roughly Ryani's size — in a present-day "
    "AI cut he reads COMPARABLE to the Frenchie; flag as a defect a kitten-tiny Leo (baby "
    "drift) or one grossly larger than her, not comparable sizing. Noticeably smaller is "
    "correct only in an explicit baby/past-era cut (real-clip cuts exempt).")


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
