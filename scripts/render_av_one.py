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
    # 체인+윙크 픽스 검증용 A — 컷마다 주체 교대(랴니↔레오) + 승자=레오
    "gansik_doduk": (
        "ai_vtuber 숏츠. 컨셉: 거실의 '간식 도둑' — 한 컷씩 주인공이 바뀌는 몽타주. 단일 공간(거실).\n"
        "막1: 할머니 손이 그릇에 간식을 담아 랴니(11살, 회색 주둥이 성견, 꼬리 없음) 앞에 놓는다. "
        "랴니가 흐뭇하게 코를 박고 먹으려 한다. (이 컷 주인공 = 랴니)\n"
        "막2: 랴니가 잠깐 고개를 든 사이, 레오(8개월 주황 태비)가 살금살금 다가와 그릇에 머리를 "
        "들이밀고 간식을 낼름낼름 먹어치운다. 동작 또렷하게. (주인공이 레오로 전환)\n"
        "막3: 랴니가 다시 그릇을 보니 텅 비었다. 어리둥절·억울한 표정으로 빈 그릇을 내려다본다. "
        "(주인공 = 랴니)\n"
        "막4(반전): 레오는 시치미를 뚝 떼고 입맛을 다시며 딴청을 부린다 — 다 먹은 건 레오였다. "
        "(주인공 = 레오)\n"
        "★승자 = 결국 간식을 다 차지한 레오. 마지막 윙크는 레오가 한다(wink_subject=leo). 랴니는 "
        "착각·억울한 당한 쪽이라 윙크하지 않는다.\n"
        "★컷마다 주인공이 랴니↔레오로 바뀌므로 각 컷은 그 컷 주인공의 또렷한 장면으로 연출 — "
        "앞 컷 화면을 그대로 물려받지 말고, 주인공이 바뀌는 컷은 그 주인공이 화면 중심에 오게.\n"
        "★랴니는 present-day 성견 — ryani_solo(회색 주둥이) 고정, 어린 랴니 금지. 의상 없음(맨몸). "
        "사람은 손만(얼굴 X).\n"
        "★모션: 정적 금지 — 레오 살금→낼름, 랴니 코박기→고개들기·억울, 카메라 push_in으로 각 액션 따라가기."
    ),
    # 체인+윙크 픽스 검증용 B — 컷마다 주체 교대(레오↔랴니) + 승자=랴니
    "bangseok": (
        "ai_vtuber 숏츠. 컨셉: 거실 '명당 방석 사수 작전' — 주인공이 번갈아 바뀌는 몽타주. 단일 공간(거실).\n"
        "막1: 레오(8개월 주황 태비)가 랴니의 포근한 명당 방석을 차지하고 늘어지게 눕는다. (주인공 = 레오)\n"
        "막2: 랴니(11살, 회색 주둥이 성견, 꼬리 없음)가 의젓하게 작전을 편다 — 사람 손이 레오가 "
        "좋아하는 츄르 튜브를 살짝 흔들어 레오의 시선을 방석 밖으로 끈다. (주인공 = 랴니, 작전 개시)\n"
        "막3: 레오가 츄르에 홀려 방석에서 폴짝 내려와 츄르 쪽으로 향한다. (주인공 = 레오)\n"
        "막4: 그 틈에 랴니가 잽싸게 방석에 올라 의젓하게 자리를 차지하고 앉는다 — 작전 성공. (주인공 = 랴니)\n"
        "★승자 = 작전으로 명당을 되찾은 랴니. 마지막 윙크는 랴니가 한다(wink_subject=ryani). 레오는 "
        "유인당한 쪽이라 윙크하지 않는다.\n"
        "★컷마다 주인공이 레오↔랴니로 바뀌므로 각 컷은 그 컷 주인공의 또렷한 장면으로 연출.\n"
        "★랴니는 present-day 성견 — ryani_solo(회색 주둥이) 고정, ref 모드 우선해 어린 랴니로 "
        "드리프트 금지. 의상 없음(맨몸). 사람은 손만(얼굴 X).\n"
        "★윙크: 랴니 1회, 해부학적으로 자연스럽게(목 꺾임 금지).\n"
        "★모션: 정적 금지 — 레오 늘어짐→폴짝 내려옴, 랴니 작전→방석 점프·의젓하게 앉기, 두 펫 큰 동작."
    ),
    # 6/24 #1 — concept_brainstorm audience-gate 9.0 선정본(스파이 vs 관찰자)을 디렉티브化
    "spy": (
        "ai_vtuber 숏츠. 컨셉: '스파이 vs 관찰자 — 서로의 하루를 몰래 엿보다'. 단일 공간(거실), 코믹.\n"
        "랴니(11살 회색주둥이 성견, 꼬리없음)와 레오(8개월 주황태비)가 서로의 하루를 몰래 훔쳐보는 첩보극.\n"
        "막1: 레오가 가구/문틈 뒤에 살금 숨어 랴니를 '미행'하듯 빼꼼 엿본다(눈만 빠끔).\n"
        "막2: 들킬 뻔하자 잽싸게 숨고, 이번엔 랴니가 '나도 안다는 듯' 여유롭게 레오를 거꾸로 관찰.\n"
        "막3: 둘이 동시에 모퉁이에서 빼꼼 → 눈 딱 마주침 → 멈칫!\n"
        "막4: 시치미 떼고 각자 딴청, 그래도 서로 곁눈질하며 피식.\n"
        "★엿보기 컨셉이라도 정적 금지 — 살금살금 이동·빼꼼·잽싸게 숨기·고개 빼꼼 등 큰 동작과 push_in으로 첩보 긴장감을 줘라. 두 펫 모두 움직이게.\n"
        "★승자 = 끝까지 여유로웠던 랴니 → 마지막 윙크는 랴니(wink_subject=ryani).\n"
        "★랴니 present-day 성견(회색주둥이) 고정, 어린 랴니 금지. 의상 없음(맨몸). 사람 없음."
    ),
    # 6/24 #2 — PD 디렉티브: 점프 미러링 → 서유기 근두운 → 새우 무릉도원 (초현실 판타지)
    "shrimp_paradise": (
        "ai_vtuber 숏츠. 구조: 현실 점프 → 서유기 판타지 라이드. 활기차고 신나게.\n"
        "막1(현실): 거실, 랴니(11살 회색주둥이 성견, 꼬리없음)가 높은 곳의 새우 간식을 향해 폴짝 "
        "점프 — 평소 못 닿던 걸 오늘은 점프로 노린다. 레오(8개월 주황태비)가 신기하게 보다 '나도!' "
        "하며 따라 폴짝.\n"
        "막2(전환·훅): 둘이 동시에 점프한 순간, 발밑에 서유기 근두운(뭉게구름)이 뿅 생겨 둘을 태우고 "
        "두둥실 떠오른다. 둘 다 눈 동그래져 신남. (의도된 판타지 — 물리 무시 OK, 이게 훅이다)\n"
        "막3(무릉도원): 구름이 복숭아꽃 만발한 무릉도원으로 데려간다. 거기엔 둘 키만 한 큰 새우들이 "
        "덩실덩실 춤을 춘다. 랴니·레오가 새우들과 함께 신나게 점프하고 춤춘다.\n"
        "막4(절정): 행복이 터지고 — 마지막 컷 카메라 향해 행복한 윙크 1회.\n"
        "★꼬리 anatomy 그대로 — 랴니는 꼬리 없음, 레오는 꼬리 있음(스왑하지 마라).\n"
        "★의상 없음(맨몸). 랴니 present-day 성견(회색주둥이) 고정, 어린 랴니 금지. 사람 없음.\n"
        "★모션 정적 금지 — 점프·구름타기·춤 전부 큰 동작. 거실→무릉도원 장소 전환이라 각 컷 자기 still."
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
