"""8/20 08:00 AV 재렌더 — '아기 레오의 반나절 가출, 누나 냄새 따라 대모험' (PD 실화).
PD: 기존 '명당 쟁탈전'은 컨셉이 없음 → 폐기. 실화로 교체 —
  하비가 문 연 찰나 레오가 호다닥 밖으로 → 바깥세상 첫 구경 → 자꾸 '랴니(누나) 냄새 나는
  곳'을 킁킁 돌아다님 → 해질녘 문 밖에서 '레오레오' 울음 → 함미가 듣고 발견 →
  그날 이후 레오한테 목줄이 생김. 컨셉 = 레오가 누나 냄새 나는 곳을 돌아다니는 내용.
  CONCEPT_BRAINSTORM=0 AV_REF_VIDEO=0 AV_FORCE_STRONG_WINK=1 SELFHEAL_REROLL=0 \
    run_job.sh scripts/_render_av_0820_0800.py
"""
import os, sys, datetime as dt
os.environ.setdefault("CONCEPT_BRAINSTORM", "0")
os.environ.setdefault("AV_REF_VIDEO", "0")
os.environ.setdefault("AV_FORCE_STRONG_WINK", "1")
os.environ.setdefault("SELFHEAL_REROLL", "0")
sys.path.insert(0, ".")
from pathlib import Path
from agents import arc
from agents.producer import _db, _gather_context, propose_concepts, produce_and_render, _auto_upload_episode
from agents.launch import publish_at_for

TARGET = dt.date(2026, 8, 20)
SLOT = "08:00"
OLD = "S9kEGX51LGY"

DIRECTIVE = (
    "ai_vtuber 숏츠. 컨셉(PD 실화): '아기 레오의 반나절 가출 대모험 — 누나 랴니 냄새 따라서'. "
    "할머니(함미)·할아버지(하비) 집 현관/마당을 배경으로 한 뚜렷한 감정 아크(호기심→탐험→누나흔적에 안심"
    "→집찾는 울음→구조→안도). 주인공은 막내 레오, 랴니는 마지막 컷에서만 등장(대부분 레오 단독).\n"
    "★★핵심(PD 지시): 레오가 '랴니(누나) 냄새 나는 곳'을 돌아다니는 게 중심이다. 랴니 본체는 밖에 없고, "
    "레오가 누나가 앉던 자리·다니던 길의 냄새를 킁킁 따라간다. 이야기 골자를 캡션으로 분명히 전달하라.\n"
    "막1(기, intro): 함미/하비 집 현관. 사람 손이 현관문을 여는 찰나, 어린 주황 태비 레오가 호다닥 문틈으로 "
    "빠져나간다. 바깥세상 첫 구경에 눈이 동그래진 레오. 캡션 훅 '문 열린 틈에… 레오가 호다닥 가출!'.\n"
    "막2(승): 집 앞 마당/골목. 레오가 여기저기 코를 대고 킁킁— 근데 자꾸 한 방향으로. 누나 랴니가 앉던 자리, "
    "다니던 길목의 냄새를 따라간다. '어? 여기 누나 냄새 나는데…'.\n"
    "막3(승): 레오가 랴니 냄새 나는 자리에 코를 박고 두리번. 낯선 바깥이지만 누나 흔적에 조금 안심한 표정 — "
    "'누나가 여기 있었구나' 하듯.\n"
    "막4(전, hook): 해가 기울어 노을. 레오가 집 쪽을 보며 '야옹— 레오레오!' 하고 크게 운다(집을 찾는 울음). "
    "안에서 사람 손/그림자가 반응— '어? 레오 밖에 있어?!'. 캡션으로 '함미가 그 울음 듣고 발견!' 전달.\n"
    "막5(결, closer): 사람 손이 레오를 살포시 안아 든다. 안도하는 레오. 이제 레오 목에 작고 귀여운 목줄(하네스)"
    "이 생겼다 — '그날 이후 레오한테 목줄이 생겼대요'. 곁에서 누나 랴니(11살 회색주둥이 성견, 꼬리 없음, 흰 "
    "주둥이 blaze·흰 가슴·흰 턱)가 반갑게 맞아준다.\n"
    "막6(윙크): 레오가 카메라를 향해 한쪽 눈을 크고 또렷하게 찡긋 1회 — '누나 냄새 따라 모험했더니, 이제 나도 "
    "목줄 생겼다냥!'. wink_subject=leo(레오가 눈 분명히 감았다 뜨는 윙크. 개 랴니는 억지 윙크 금지).\n"
    "★캐릭터: 레오=어린 주황 태비 고양이(2025-09생, 장난꾸러기 아기티), 눈 연두/황록. 랴니=present 성견 "
    "회색주둥이, 꼬리 없음(anatomy 그대로), 흰 blaze/가슴/턱 마킹. cast=막1~4 레오 단독(랴니 없음), "
    "막5~6 레오+랴니. 막1~4의 레오는 목줄 없음, 막5의 레오는 새 목줄 있음(사건의 결과).\n"
    "★캡션 = 레오 시점 톤(설렘→호기심→안심→집찾기→안도), 이야기 골자를 또렷이. 컬러 이모지 금지(♥/♡ 허용).\n"
    "★★렌더: 각 컷은 함미집 현관/마당의 단일 공간 락, 배경 첫 프레임 고정(모핑 금지), 카메라 완전 고정"
    "(윙크만 가벼운 push_in). 활기는 레오 몸/표정 모션으로. 사람은 얼굴 없이 손만. 상상·변신 컷 금지 — "
    "실제 집 앞의 실사감. 레오 마킹/크기 전 컷 일관."
)


def pcb(m):
    print("[av0820_0800]", m, flush=True)


if __name__ == "__main__":
    con = _db()
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    print("directive set 레오 반나절 가출", flush=True)
    context = _gather_context(con, TARGET)
    concepts = propose_concepts(TARGET, context, style_filter="ai_vtuber", progress_cb=pcb)
    if not concepts:
        print("NO CONCEPT", flush=True); sys.exit(1)
    c = concepts[0]
    t = c.get("title"); t = t.get("ko") if isinstance(t, dict) else t
    print("TITLE:", t, "NCUTS:", len(c.get("cuts") or []), flush=True)
    outs = produce_and_render([c], TARGET, progress_cb=pcb)
    out = outs[0] if outs else None
    if not out:
        row = con.execute("SELECT output_video_path FROM cards WHERE date=? AND render_style='ai_vtuber' "
                          "ORDER BY updated_at DESC LIMIT 1", (TARGET.isoformat(),)).fetchone()
        out = row[0] if row and row[0] else None
    print("OUT:", out, flush=True)
    if not out:
        print("NO OUTPUT", flush=True); sys.exit(1)
    pub = publish_at_for(TARGET, SLOT)
    vid = _auto_upload_episode(con, Path(out).resolve(), TARGET, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED {vid} pub={pub}", flush=True)
    if vid and vid != OLD:
        from youtube.upload import veto_video
        veto_video(OLD, delete=False); print(f"VETOED old {OLD}", flush=True)
    print("DONE_0820_0800", flush=True)
