"""8/21 21:00 AV 재렌더 (PD 8/21 리뷰: '이게 뭔 컨셉이야. 어디가 런웨이야 + 랴니 목뒤 흰마크 + 캡션 재밌게').
옛 2NzFPbDJA78='거실 런웨이 위글워킹' — 프레임 확인 결과 (1)런웨이가 시각적으로 전혀 없음(그냥 빈 거실을
둘이 걸어다님, '런웨이'는 캡션에만) (2)랴니 목뒤/등에 흰 마크가 생김(캐논 위반: 흰 마킹은 턱/가슴/발끝만)
(3)캡션이 밋밋. → 런웨이를 시각적으로 못박고(카펫 런웨이+스포트라이트+플래시), nape 클린블랙 고정,
패션쇼 중계톤 재치 캡션으로 재렌더 → 21:00 재예약 → 옛본 veto.
  CONCEPT_BRAINSTORM=0 AV_REF_VIDEO=0 AV_CLEAN_CHAR_REF=1 AV_FORCE_STRONG_WINK=1 SELFHEAL_REROLL=0 \
    sudo -u rianileo bash deploy/run_job.sh scripts/_render_av_0821_2100_runway.py
"""
import os, sys, datetime as dt
os.environ.setdefault("CONCEPT_BRAINSTORM", "0")
os.environ.setdefault("AV_REF_VIDEO", "0")
os.environ.setdefault("AV_CLEAN_CHAR_REF", "1")   # base_both character-lock → nape 흰마크 드리프트 차단
os.environ.setdefault("AV_FORCE_STRONG_WINK", "1")
os.environ.setdefault("SELFHEAL_REROLL", "0")
sys.path.insert(0, ".")
from pathlib import Path
from agents import arc
from agents.producer import _db, _gather_context, propose_concepts, produce_and_render, _auto_upload_episode
from agents.launch import publish_at_for

TARGET = dt.date(2026, 8, 21)
SLOT = "21:00"
OLD = "2NzFPbDJA78"

DIRECTIVE = (
    "ai_vtuber 숏츠. 컨셉: '우리집 거실이 진짜 패션쇼 런웨이!' — 랴니와 레오가 런웨이를 워킹하는 패션쇼. "
    "★★런웨이가 시각적으로 분명해야 한다(옛본은 그냥 빈 거실을 걸어서 '런웨이가 어디냐'는 지적을 받음). "
    "거실 바닥 한가운데 **길게 뻗은 핑크/레드 카펫 런웨이**를 깔고, 카펫 양옆으로 **무대 스포트라이트**, "
    "배경엔 은은한 관객 실루엣과 카메라 **플래시 반짝임**으로 '패션쇼 무대'임을 못박아라. 펫은 그 카펫 "
    "런웨이 위를 카메라 쪽으로 당당하게 걸어와 끝에서 **포즈**를 취한다.\n"
    "막1(기, intro/훅): 어두운 무대에 런웨이 카펫 스포트라이트가 탁 켜진다. 캡션 훅으로 '오늘의 무대는… "
    "거실 런웨이! 랴니&레오 패션위크 개막'.\n"
    "막2(승): 1번 모델 **랴니**(11살 성견, 꼬리 없음) 등장 — 특유의 뒤뚱 '위글워킹'으로 카펫 런웨이를 "
    "당당하게 워킹. 캡션 '1번 모델 랴니 누나, 시그니처 위글워킹 입장!'.\n"
    "막3(승): 2번 모델 **레오**(주황 태비) 등장 — 꼬리를 빳빳이 높이 세운 도도한 캣워크로 사뿐사뿐. "
    "캡션 '2번 모델 레오, 도도 캣워크 😼'.\n"
    "막4(전, hook/펀치): 둘이 런웨이 끝에서 만나 카메라를 향해 나란히 포즈 — 플래시가 팡팡 터진다. "
    "캡션 '런웨이 끝 합동 포즈… 플래시 팡팡! 📸'.\n"
    "막5(결, 윙크/closer): 그 포즈 그대로 레오가 카메라 향해 한쪽 눈을 크고 또렷하게 찡긋 1회. "
    "캡션 '오늘의 대상은…? (윙크)'. wink_subject=leo(개 랴니는 억지 윙크 금지).\n"
    "★캐릭터 고정: 랴니=검정 프렌치불독, **흰 마킹은 턱/주둥이 blaze·가슴·발끝에만**. "
    "★★목 뒤·뒷목·등은 순수 검정 — 흰 반점/흰 마크 절대 없음(옛본이 목뒤 흰마크가 생겨 캐논 위반). "
    "꼬리 없음(anatomy 그대로). 레오=주황 태비 고양이, 꼬리 있음(높이 세워 캣워크), 눈 연두/황록. "
    "cast=막1 무대만/막2 랴니 워킹(레오 대기)/막3 레오 워킹/막4~5 둘 다.\n"
    "★캡션=패션쇼 생중계 톤으로 재치있게(모델 넘버·시그니처 워킹·심사평·대상 발표), 웃음 포인트 또렷. "
    "컬러 이모지 최소, ♥/♡ 허용.\n"
    "★★렌더: 각 컷 i2v로 자기 still(런웨이 무대 still)을 first_frame으로 깔고 애니메이트 — 배경 통째 "
    "생성 ref(Omni Reference) 모드 금지. 런웨이 무대 배경은 첫 프레임 그대로 고정(가구/조명 모핑 금지), "
    "카메라 완전 고정, 활기는 펫의 워킹·포즈·윙크 모션으로. 단일 무대 공간에서만."
)


def pcb(m):
    print("[av0821_2100]", m, flush=True)


if __name__ == "__main__":
    con = _db()
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    print("directive set 거실 런웨이 패션쇼", flush=True)
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
    print("DONE_0821_2100", flush=True)
