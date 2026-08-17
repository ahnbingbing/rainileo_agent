"""8/19 12:30 AV 재렌더 — '비 오는 날, 산책 못 가 시무룩한 랴니' (PD 피드백).
PD: (1) 랴니가 왜 시무룩인지 나와야 함 → '밖에 비가 와요'(산책 취소)로 이유 제시.
(2) 마지막 컷 랴니 마킹이 너무 커짐(드리프트) → 전 컷 마킹 고정.
(3) 랴니가 왜 윙크 안 해? → 윙크 주체 = 랴니(레오 아님).
  CONCEPT_BRAINSTORM=0 AV_REF_VIDEO=0 AV_FORCE_STRONG_WINK=1 run_job.sh scripts/_render_av_0819_1230.py
"""
import sys, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents import arc
from agents.producer import _db, _gather_context, propose_concepts, produce_and_render, _auto_upload_episode
from agents.launch import publish_at_for

TARGET = dt.date(2026, 8, 19)
SLOT = "12:30"
OLD = "Zkrwq9ziMUk"

DIRECTIVE = (
    "ai_vtuber 숏츠. 컨셉: '비 오는 날, 산책 못 가서 시무룩한 랴니'. 단일 공간(거실+창가), 뚜렷한 감정 아크 "
    "+ 캐릭터 대비(산책 좋아하는 랴니 vs 집도 좋은 막내 레오).\n"
    "★★핵심(PD 지시): 랴니가 시무룩한 '이유'가 화면+캡션에 분명해야 한다 — **창밖에 비가 주룩주룩 오고, "
    "그래서 산책이 취소돼서** 랴니가 풀 죽은 것. 첫 컷에서 '밖에 비가 와요'를 캡션으로 못박아라.\n"
    "막1(기, intro): 창가. 창밖에 비가 내린다(빗방울/젖은 유리). 랴니(11살 회색주둥이 성견, 꼬리 없음, "
    "흰 주둥이 blaze·흰 가슴·흰 턱)가 창밖 비를 보며 시무룩하게 앉아 있다. 캡션 훅 '밖에 비가 와요…'.\n"
    "막2(승): 산책 대장 랴니가 완전히 풀 죽어 바닥에 턱을 대고 엎드린다 — '오늘 산책은 또 취소인가봐'.\n"
    "막3(승, hook): 막내 레오(8개월 주황태비)가 슬쩍 다가와 코를 비비거나 앞발로 톡톡 — 위로하듯 장난.\n"
    "막4(결, closer): 둘이 거실에서 나란히 붙어 앉는다. 랴니 표정이 조금 풀린다. 랴니 마킹(흰 blaze/가슴/턱, "
    "꼬리 없음)은 이 컷에서도 앞 컷과 **완전히 동일**해야 한다(직전 버전은 마지막 컷에서 랴니 마킹이 너무 "
    "커졌다 — 절대 금지, 크기·위치·모양 고정).\n"
    "막5(클로저): **랴니가** 카메라를 향해 기대에 찬 초롱초롱한 눈빛으로 한쪽 눈을 살짝 깜빡인다(자연스러운 "
    "블링크/윙크면 충분 — 개가 부자연스럽게 억지로 감는 컷 금지, 렌더가 어려우면 또렷한 눈맞춤으로). 캡션 '비 "
    "그치면 꼭 산책 가자냥! ☔→☀'.\n"
    "★클로저 주체 = 랴니(레오 아님). 랴니가 카메라를 보며 기대 담은 표정/살짝 깜빡. 무리한 강제 윙크로 캐릭터가 "
    "깨지면 안 되니 자연스럽게.\n"
    "★캡션 = 랴니 시점 감정 톤(시무룩→위로→기대), 첫 컷은 '비 와서 산책 취소'를 명확히. 컬러 이모지 금지(♥/♡·☔·☀ 허용).\n"
    "★★렌더: 단일 거실/창가 락, 배경 첫 프레임 고정(모핑 금지), 카메라 완전 고정(윙크만 가벼운 push_in). "
    "빗물 창밖은 배경으로 은은하게, 활기는 펫 몸/표정 모션으로. 랴니 마킹 전 컷 동일.\n"
    "★레오 8개월 주황태비(2025-09생), 랴니 present 성견 회색주둥이 꼬리없음(어린 랴니 금지). 꼬리 anatomy "
    "그대로(랴니 없음·레오 있음). 사람 없음(손만). 상상·변신 컷 금지 — 실제 거실의 실사감."
)


def pcb(m):
    print("[av0819_1230]", m, flush=True)


if __name__ == "__main__":
    con = _db()
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    print("directive set 비오는날 시무룩랴니", flush=True)
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
    print("DONE_1230", flush=True)
