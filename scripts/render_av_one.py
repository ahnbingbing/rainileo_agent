"""Produce + render ONE ai_vtuber episode for a target date from a prescriptive
PD concept directive, bypassing the launch slot machinery (AV lane may be paused).

Sets the PD /concept directive (highest-priority arc_directive), runs the
Writer→Director (with the current director_shots.md fixes), then produce_and_render
(stills best-of-5 → Seedance → captions → assemble). CONCEPT_BRAINSTORM is forced
off so the Writer follows the PD directive faithfully instead of a brainstorm winner.

  CONCEPT_BRAINSTORM=0 .venv/bin/python -m scripts.render_av_one hawaii --dry-run
  CONCEPT_BRAINSTORM=0 .venv/bin/python -m scripts.render_av_one homecam
"""
import datetime as dt
import sys

from agents import arc
from agents.producer import _db, _gather_context, propose_concepts, produce_and_render

TARGET = dt.date(2026, 6, 21)

DIRECTIVES = {
    # #1 — 비 오는 날 → 하와이 상상 → 현실 복귀 (현실→상상→현실 3막, 의상 PD 승인)
    "hawaii": (
        "ai_vtuber 숏츠. 구조: 현실→상상→현실 3막, 활기차고 감정선 분명하게.\n"
        "막1(현실, 차분): 비 오는 날. 창밖에 비가 내려 산책을 못 나가, 거실에서 "
        "시무룩하게 창밖을 바라보는 랴니(11살, 회색 주둥이 성견, 꼬리 없음)와 "
        "레오(8개월 주황 태비). 톤 다운.\n"
        "막2(상상, 화려): 화면이 화사한 하와이 해변으로 전환. 랴니와 레오가 하와이안 "
        "셔츠를 입고 꽃목걸이(레이)를 걸고 작은 선글라스를 쓴 채 비치 선베드에 나란히 "
        "앉아 작은 우산 꽂힌 트로피컬 음료를 즐긴다. 야자수·파란 바다·밝은 햇살, 신나는 "
        "휴양 무드. ★이 상상 컷에 한해 의상(셔츠/레이/선글라스) 착용 허용 — PD 승인된 "
        "예외. 카메라는 push_in/팬으로 에너지를 주고 두 펫 모두 활발히 움직인다.\n"
        "막3(현실 복귀): '간식 먹자~' 할머니 목소리에 번쩍 현실로. 다시 거실, 의상 없이 "
        "맨몸으로 그릇 앞에서 간식을 맛있게 먹는 랴니와 레오. 마지막 컷: 고개를 들며 "
        "카메라를 향해 행복하게 윙크 1회.\n"
        "랴니는 present-day 성견이므로 ryani_solo(회색 주둥이) 레퍼런스로 — 절대 어린 "
        "랴니로 그리지 말 것."
    ),
    # #2 — 홈캠 관찰: 가족 외출 후 아이돌 댄스 (단일 공간, 홈캠 미감)
    "homecam": (
        "ai_vtuber 숏츠. 컨셉: 홈캠(CCTV) 관찰 — 단일 공간(거실) 고정.\n"
        "가족이 모두 외출한 시각, 거실 고정 홈캠 시점(코너에서 내려다보는 느낌, 살짝 "
        "광각, 화면 한쪽에 타임스탬프 느낌). 의상 없음(맨몸).\n"
        "막1: 랴니(11살, 회색 주둥이 성견, 꼬리 없음)와 레오(8개월)가 홈캠을 신기한 듯 "
        "빤히 쳐다보며 천천히 카메라 쪽으로 다가온다.\n"
        "막2(훅): 갑자기 둘이 카메라 앞에서 신나게 아이돌 군무를 춘다 — 동작 크고 "
        "리드미컬하게, 둘 다 활발하게. 홈캠이라 카메라는 고정(locked static)이지만 펫들의 "
        "큰 동작으로 에너지를 만든다.\n"
        "막3(반전 유머): 아무 일도 없었다는 듯 뒤돌아 제자리로 가서 바닥에 털썩 눕는다.\n"
        "랴니는 present-day 성견이므로 ryani_solo(회색 주둥이) 레퍼런스로 — 절대 어린 "
        "랴니로 그리지 말 것."
    ),
    # 침입자 re-render (item 2): 정적 모션 + 어린 랴니 문제 수정판
    "chimipja": (
        "ai_vtuber 숏츠. 컨셉: 거실의 '침입자 소동' — 코믹 액션.\n"
        "레오(8개월 주황 태비)가 거실에서 작은 움직임/그림자를 침입자로 착각해 특급 경보를 "
        "발령하고, 랴니(11살, 회색 주둥이 성견, 꼬리 없음)가 '불독 엄마 모드'로 상황을 "
        "정리하는 1막 코믹 액션. 단일 공간(거실).\n"
        "★펀치라인은 '캐릭터 대비'로 맺는다 — 끝에서 '범인은 사실 바람/햇빛이었다' 같은 김 "
        "빠지는 정체-공개로 끝내지 마라(어색하다). 호들갑 떠는 레오 vs 의젓하게 별일 아니란 "
        "듯 자리를 지키는 불독엄마 랴니의 온도차가 웃음 포인트다. 레오는 여전히 경계 태세, "
        "랴니는 다 안다는 듯 여유로운 마지막 표정.\n"
        "★모션: 액션 컨셉이므로 절대 정적이면 안 된다. 추격/덮침/경보 훅 컷은 카메라가 "
        "push_in/팬으로 액션을 따라가고, 두 펫 모두 큰 동작(레오 살금→덮침, 랴니 귀 뒤로→"
        "플레이바우→짖음)으로 화면에 항상 두 마리가 움직이게 하라. 랴니가 가만히 서 있는 "
        "들러리가 되지 않게.\n"
        "★랴니는 present-day 성견 — ryani_solo(회색 주둥이) 고정, i2v보다 ref 모드 우선해 "
        "어린 랴니로 드리프트하지 않게.\n"
        "★엔딩: 랴니의 윙크 1회로 마무리한다 — 포즈는 자유롭게(앉아서든, 누워서든, 편하면 "
        "벌러덩 배를 보이며 해도 좋다), 한쪽 눈을 찡긋 감았다 뜬다. 단 하나 지킬 것은 "
        "해부학적 자연스러움: 머리와 목이 몸통과 자연스럽게 이어진, 실제 강아지의 가동범위 "
        "안의 자세여야 한다. (직전 렌더는 벌러덩 자세 자체는 괜찮았는데 목이 180도 꺾여 "
        "좀비처럼 보였다 — 포즈가 아니라 그 불가능한 목 회전이 문제였다.)\n"
        "의상 없음(맨몸)."
    ),
}


def pcb(m):
    print("P:", m, flush=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in DIRECTIVES:
        print("usage: render_av_one.py {hawaii|homecam} [--dry-run]")
        return
    slug = sys.argv[1]
    dry = "--dry-run" in sys.argv
    con = _db()
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVES[slug])
    print(f"directive set for {TARGET} ({slug}); dry_run={dry}", flush=True)
    context = _gather_context(con, TARGET)
    concepts = propose_concepts(TARGET, context, style_filter="ai_vtuber", progress_cb=pcb)
    if not concepts:
        print("NO CONCEPT", flush=True)
        return
    c = concepts[0]
    t = c.get("title")
    t = t.get("ko") if isinstance(t, dict) else t
    print("TITLE:", t, flush=True)
    print("NCUTS:", len(c.get("cuts") or []), flush=True)
    outs = produce_and_render([c], TARGET, progress_cb=pcb, dry_run=dry)
    print("RENDER_OUTS:", outs, flush=True)


if __name__ == "__main__":
    main()
