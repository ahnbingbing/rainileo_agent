"""Author the AV (ai_vtuber) storyboard for '랴니의 첫 수영 도전기' via the pipeline
(PD directive → propose_concepts ai_vtuber → Writer/Director). NO render — just
produce + print the storyboard so we validate cheaply (best-of-5 stills → Giri
pick) before the ~$50 Seedance render.

Grounded in Ryani's real 2018 first-swim footage
(data/assets/clips/2018/med_2018_06_20_113246_icloud_56c9e99f.mp4). This is the
ORIGIN of her water-mania — younger Ryani, her first brave plunge.
"""
import datetime as dt, json, os
from agents.producer import _db, _gather_context, propose_concepts
from agents import arc

TARGET = dt.date(2026, 6, 16)
DIRECTIVE = (
    "랴니의 첫 수영 도전기 (ai_vtuber, 단일 야외 수영장/데크 SCENE LOCK, 마지막 윙크 한 컷). "
    "메모리레인: 어린 랴니(약 2~3살, 통통한 퍼피)가 생애 처음 물에 도전하는 그 여름. "
    "현재 물-매니아 랴니의 '시작점' 이야기. 따뜻하고 응원하는 vlog 톤. "
    "스토리 비트: "
    "1) 주저 — 나무 데크 계단 끝에 앉아 출렁이는 물을 빤히 내려다본다(설렘+긴장). "
    "2) 결심 — 앞발 하나를 물에 톡 담갔다가 움찔, 그래도 다시. "
    "3) 첫 입수 — 큰맘 먹고 풍덩, 물보라. "
    "4) 수영 — 코를 들고 앞발로 첨벙첨벙 개헤엄, 점점 신나한다(물-매니아 각성). "
    "5) 기어오름 — 풀 가장자리에 앞발을 걸치고 끙— 올라온다(해냈다). "
    "6) 윙크 — 흠뻑 젖은 채 카메라를 보며 느리게 윙크(또 하고 싶어!). "
    "근거: 랴니는 실제로 물을 무서워하지 않고 좋아하게 된 강아지 — 이 영상이 그 첫 도전. "
    "랴니 = 작고 여성스러운 검은 프렌치불독, 꼬리 없음(ABSOLUTELY no tail), "
    "얇은 blaze(Boston 패턴 아님), 턱/가슴/발끝 흰 마킹, she/her. "
    "어린 시절이라 지금보다 살짝 통통/말랑하지만 같은 개. 의인화 의상/수영복 금지, 사람 얼굴 노출 금지. "
    "레오(고양이)는 물을 피하므로 등장하지 않거나 풀가에서 드라이하게 지켜보는 최소 카메오만."
)


def pcb(m):
    print("P:", m, flush=True)


def main():
    con = _db()
    print("gathering context...", flush=True)
    context = _gather_context(con, TARGET)
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    # PD directive IS the premise — don't let brainstorm replace it.
    os.environ["CONCEPT_BRAINSTORM"] = "0"
    try:
        print("proposing ai_vtuber storyboard (첫 수영 도전기)...", flush=True)
        concepts = propose_concepts(TARGET, context, style_filter="ai_vtuber",
                                    progress_cb=pcb)
        if not concepts:
            print("NO CONCEPT", flush=True); return
        c = concepts[0]
        out = "/tmp/av_first_swim_concept.json"
        with open(out, "w") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        t = c.get("title"); t = t.get("ko") if isinstance(t, dict) else t
        print("TITLE:", t, flush=True)
        for i, cut in enumerate(c.get("cuts") or []):
            print(f"--- cut{i+1} [{cut.get('tag') or cut.get('beat')}] "
                  f"seedance_mode={cut.get('seedance_mode')} "
                  f"dur={cut.get('duration_seconds')}", flush=True)
            print("   regen:", (cut.get('regen_prompt') or cut.get('regen_direction') or '')[:180], flush=True)
            print("   motion:", (cut.get('motion_prompt') or '')[:180], flush=True)
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
