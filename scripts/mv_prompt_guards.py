"""MV 스틸/i2v 생성 프롬프트 — 재사용 가드 모듈 (파이프라인 계약).

MV 결함의 근본은 i2v 클립이 아니라 스틸 생성 프롬프트의 전역 스캐폴딩에 있다
(notes/mv_agent_feedback_log.md §8.14, 메모리 mv_generator_prompt_footguns).
새 MV의 still-generator / i2v 스캐폴딩은 이 상수를 import 해서 verbatim으로 박아라 —
매번 다시 유도하다 풋건에 빠지지 말고. 감꼭지 MV 수정에서 검증됨.

쓰는 법:
    from scripts.mv_prompt_guards import ANTI_PIP, SOLO, NO_HUMANS, HANDS_EDGE, prop_lock, character_sheet

    # 솔로 컷(한 마리만):  scene + SOLO("orange tabby cat") + ANTI_PIP
    # 깨끗한 스튜디오 댄스:  scene + NO_HUMANS
    # 소품 등장 컷:        scene + prop_lock("감꼭지 calyx", "...reference...") + ANTI_PIP
    # 두 펫 캐릭터 락:      scene + character_sheet("ryani") + character_sheet("leo")

★★ 모델별 프롬프팅 — 공식 가이드는 `notes/model_prompting_guides.md`를 보라(1차 출처 영속).
  이 모듈의 가드는 **모델에 따라 형태가 달라야 한다.** 핵심 갈림길 = 부정(negation) 처리:

  • NANO-BANANA / FLUX / Seedance = 부정을 싫어함(전용 negative 필드 없음). "NO dog/NO humans/no tail"는
    dog·tail 단어에 반응해 **오히려 그린다**(12컷 엔싱크 사람 유입·랴니 꼬리 환각이 이 증상). → 아래
    **긍정형 변주(`*_POS`)**를 써라. 원칙(가이드 §nano-banana): ①짧게(주체+행동+배경+스타일, character_sheets
    전문 때려넣지 말고 꼭 필요한 마킹 한두 개) ②부정 대신 **원하는 상태를 긍정 묘사**("bare head"/"Leo alone"/
    "empty set") ③한 샷에 다 X — 배경→메인→요소 단계적.
  • GPT-IMAGE(OpenAI) / Claude = 부정이 **1급 지원**("state exclusions explicitly"). 아래 원래 부정형
    상수(ANTI_PIP/SOLO/NO_HUMANS)를 그대로 써도 강인하다. PD가 "애니틱" 싫어하면 gpt-image 실사 edit이 기본.

  한 줄: **nano/flux/seedance 경로 → `*_POS` 긍정형 / gpt-image·claude 경로 → 원래 부정형.**
  [[mv_generator_prompt_footguns]]
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ── 풋건 가드 (§8.14) ────────────────────────────────────────────────
# 레퍼 이미지를 생성 입력으로 주면 nano-banana가 PIP 박스로 박는다 → 항상 함께.
ANTI_PIP = ("This is ONE single full-frame photograph ONLY — no inset, no picture-in-picture, "
            "no thumbnail, no corner box, no reference image or product shot anywhere in the frame. ")

# "ONLY TWO animals(개1+고양이1)" 전역 문구는 솔로 컷에도 둘째 펫을 불러들인다. 솔로 컷은 이걸로.
def SOLO(animal: str = "orange tabby cat") -> str:
    return (f"ONLY ONE single animal in the ENTIRE frame — one {animal}, completely ALONE. "
            "NO dog, NO second cat, NO other animal anywhere including the background, and NONE "
            "enters the frame at any moment. ")

# 깨끗한 스튜디오/AV 컷: 사람/손 전무 (전역 "가장자리 박수 손"은 얼어붙은 손을 박는다).
NO_HUMANS = ("NO humans anywhere in the frame — NO hands, NO arms, NO fingers, NO people, nobody. "
             "Only the pets on a clean empty backdrop. ")

# 손을 꼭 넣어야 하는 컷만: 얼굴/머리 없이 가장자리 손만 (하지만 소품 상호작용이면 §8.14대로 손 제거가 낫다).
HANDS_EDGE = ("Humans appear ONLY as hands/arms at the frame edge — NEVER any human face, head, hair or body. ")


# ── nano-banana / FLUX / Seedance 긍정형 변주 (notes/model_prompting_guides.md §0.1) ─────
# 부정 싫어하는 모델엔 위 ANTI_PIP/SOLO/NO_HUMANS 대신 이 긍정형을 써라 — 원하는 '상태'를 직접 묘사.
ANTI_PIP_POS = ("This is one single full-frame photograph that fills the frame edge to edge — "
                "a clean, straight-on photo of the scene itself. ")


def SOLO_POS(animal: str = "orange tabby cat") -> str:
    return (f"The frame holds exactly one animal: a single {animal}, alone on a clean backdrop "
            "with open, empty space all around it. ")


NO_HUMANS_POS = ("The scene shows only the pets on a clean, empty backdrop — just the animals, "
                 "with open space where no one else stands. ")


def prop_lock(prop: str, ref_desc: str = "", not_this: str = "a whole round fruit") -> str:
    """소품 모양은 텍스트만으론 안 잡힌다 — 레퍼 이미지와 함께 이 문구를. (§8.2)"""
    s = (f"The {prop} stays EXACTLY consistent in every shot — same shape, same size relative to the "
         f"pets' heads, never morphing. It is NOT {not_this}. ")
    if ref_desc:
        s += f"Match it EXACTLY to the reference image: {ref_desc}. "
    return s


# 생식기/하반신 노출 회피 (§6): 뒷발로 서는 포즈는 가슴 위로 크롭.
CHEST_UP = ("Frame the pets CLOSE from the upper chest up — their lower belly, hips, groin and hind legs "
            "are COMPLETELY OUT of frame; no groin or genital area anywhere. The female French bulldog "
            "has a smooth featureless underbelly and NO tail. ")

# 밋밋한 배경 채우기 (§8.15): 주제 소품이 배경에 부유.
def bg_float(prop: str = "little 감꼭지 calyxes and small persimmons") -> str:
    return (f"In the BACKGROUND behind the pets, several {prop} FLOAT and TUMBLE playfully through the "
            "air — soft blurred bouncing elements that add lively motion. ")


_SHEETS = _ROOT / "agents/prompts/character_sheets.md"


def character_sheet(_pet: str = "") -> str:
    """character_sheets.md 전문을 주입하라 (§8.1, 요약 금지). 이 헬퍼는 파일 경로를 반환해
    호출부가 전문을 읽어 펫별로 박게 한다. 짧은 한 줄 설명은 마킹/골격 드리프트의 근본."""
    return _SHEETS.read_text(encoding="utf-8") if _SHEETS.exists() else ""


__all__ = ["ANTI_PIP", "SOLO", "NO_HUMANS", "HANDS_EDGE", "CHEST_UP",
           "ANTI_PIP_POS", "SOLO_POS", "NO_HUMANS_POS",
           "prop_lock", "bg_float", "character_sheet"]
