"""Generate the AV (ai_vtuber) storyboard for '레오 방역반장' via the pipeline
(PD directive → propose_concepts ai_vtuber → Writer/Director). NO render — just
produce + print the storyboard (cuts, regen_prompt, motion_prompt, seedance_mode)
so we can validate cheaply (best-of-5 stills) before the $50 Seedance render.
"""
import datetime as dt, json, os
from agents.producer import _db, _gather_context, propose_concepts
from agents import arc

TARGET = dt.date(2026, 6, 16)
DIRECTIVE = (
    "레오 방역반장 (ai_vtuber, 현실→상상→현실 + 마지막 윙크 한 컷). "
    "현실: 거실 바닥/벽의 작은 벌레(점)를 레오가 발견, 동공 확장, 다급한 '냐옹냐옹' 경보로 사람을 부른다. "
    "상상: 방이 네온 작전실로 변해 레오가 스포트라이트 속 방역 사령관 — 서치라이트가 훑고, 만화처럼 큰 "
    "벌레가 타깃, 레오가 앞발로 'GO' 신호를 준다. "
    "현실: 다시 평범한 거실, 레오는 벽을 보고 소리만 지르고 사람이 한숨 쉬며 휴지로 벌레를 잡는다. "
    "윙크: 레오가 카메라를 보며 느리게 윙크 — '임무 완수'(정작 발 하나 안 댐). "
    "근거: 레오의 실제 성향 — 벌레를 주시하며 냐옹냐옹 사람을 불러 잡게 한다. "
    "거실 단일 공간 SCENE LOCK. 레오=8개월 오렌지 태비(나이/마킹 정확), 코 흉터. 의인화 의상 금지."
)


def pcb(m):
    print("P:", m, flush=True)


def main():
    con = _db()
    print("gathering context...", flush=True)
    context = _gather_context(con, TARGET)
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    # PD directive is the premise — don't let brainstorm replace it.
    os.environ["CONCEPT_BRAINSTORM"] = "0"
    try:
        print("proposing ai_vtuber storyboard (방역반장)...", flush=True)
        concepts = propose_concepts(TARGET, context, style_filter="ai_vtuber",
                                    progress_cb=pcb)
        if not concepts:
            print("NO CONCEPT", flush=True); return
        c = concepts[0]
        out = ROOT_OUT = "/tmp/av_pestcontrol_concept.json"
        with open(out, "w") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        t = c.get("title"); t = t.get("ko") if isinstance(t, dict) else t
        print("TITLE:", t, flush=True)
        for i, cut in enumerate(c.get("cuts") or []):
            print(f"--- cut{i+1} [{cut.get('tag') or cut.get('beat')}] "
                  f"seedance_mode={cut.get('seedance_mode')} dur={cut.get('duration_seconds')}", flush=True)
            print("   regen:", (cut.get('regen_prompt') or cut.get('regen_direction') or '')[:160], flush=True)
            print("   motion:", (cut.get('motion_prompt') or '')[:160], flush=True)
            for cap in (cut.get('captions') or []):
                print("   cap:", cap.get('ko'), flush=True)
        print("SAVED:", out, flush=True)
    finally:
        con.execute("DELETE FROM pd_concept_directives WHERE target_date=? AND directive=?",
                    (TARGET.isoformat(), DIRECTIVE))
        con.commit()
        print("directive cleaned up", flush=True)


if __name__ == "__main__":
    main()
