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
           "prop_lock", "bg_float", "character_sheet"]
