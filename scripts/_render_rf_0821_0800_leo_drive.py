"""8/21 08:00 RF — 빈 슬롯 채우기 (PD 8/21 리뷰: '왜 3개만 생겼지').
근본=8/21 08:00 RF가 self-heal 6라운드 전멸(디스크풀 render_error + memory-lane/물 고착)로 빈 슬롯.
PD 지시=신선한 '레오 첫 바깥 나들이'(2026-08-16 outing) footage로 채워라(물 테마 제외).
이 컷=레오의 첫 드라이브, 차 대시보드에서 창밖 꽉 막힌 도로를 신기하게 구경(신선·미사용 클립).
단일 클립 + narrator 멀티씬 캡션(scenes[])을 recaption_finish로 강제 → 08:00 예약(교체할 옛 영상 없음).
  sudo -u rianileo bash deploy/run_job.sh scripts/_render_rf_0821_0800_leo_drive.py
"""
import sys, json, subprocess, glob, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _render_realfootage_direct, _auto_upload_episode
from agents.launch import publish_at_for

DATE = dt.date(2026, 8, 21)
SLOT = "08:00"
OLD = None  # 빈 슬롯 — veto 대상 없음
CLIP = "med_2026_08_16_123708_slack_1c649187"  # 레오 차 대시보드, 창밖 정체도로 구경 (44.6s, 신선)

concept = {
    "title": "레오의 생애 첫 드라이브 — 창밖 세상이 궁금해",
    "narrative_oneliner": "레오가 태어나서 처음 차를 탔어요. 대시보드에 올라 창밖 꽉 막힌 도로를 구경하느라 눈을 못 떼네요.",
    "subjects": ["leo"], "tone": {"primary": "warm_playful", "intensity": 0.55},
    "bgm_mood": "playful_upbeat", "cuts": [
        {"tag": "c1", "asset_id": CLIP, "trim_start": 2.0, "duration_seconds": 18.0,
         "captions": [{"start": 0.1, "end": 18.0, "ko": "placeholder", "en": "p"}]},
    ],
}

# narrator 멀티씬 캡션(프레임 그라운딩: 레오가 대시보드서 창밖 정체도로를 계속 구경) — 서사 프레이밍은
# 프레임서 추론 불가라 recaption_finish로 강제.
FINAL = {
    "c1": {"scenes": [
        {"start": 0.1, "end": 4.5, "ko": "레오, 태어나서 첫 드라이브 나가는 날 🚗",
         "en": "Leo's very first car ride 🚗"},
        {"start": 4.5, "end": 9.0, "ko": "창밖에 꽉 막힌 도로가 펼쳐지자… 눈이 똥그래",
         "en": "Eyes go wide at the traffic jam outside"},
        {"start": 9.0, "end": 13.5, "ko": "저 많은 차들은 다 어디 가는 거냥…?",
         "en": "Where in the world is everyone going…?"},
        {"start": 13.5, "end": 18.0, "ko": "구경이 너무 재밌어 창밖에서 눈을 못 떼요",
         "en": "Too busy sightseeing to look away"}]},
}


def pcb(m):
    print("[rf0821_0800]", m, flush=True)


if __name__ == "__main__":
    con = _db()
    out, report, card_id = _render_realfootage_direct(concept, DATE, con, progress_cb=pcb)
    print(f"CARD={card_id} OUT={out}", flush=True)
    if not out:
        print("RENDER FAILED", flush=True); sys.exit(1)
    wds = sorted(glob.glob(f"data/tmp/cameraman_{str(card_id).split('-')[0]}_*"), reverse=True)
    wd = Path(wds[0]) if wds else None
    if not wd:
        print("no workdir — using render output as-is", flush=True)
        final_out = Path(out)
    else:
        cp = wd / "final_caps.json"
        cp.write_text(json.dumps(FINAL, ensure_ascii=False, indent=2), encoding="utf-8")
        final_out = Path("data/output/episodes/episode_0821_0800_final.mp4")
        rr = subprocess.run([sys.executable, "-m", "scripts.recaption_finish", "--workdir", str(wd),
                             "--captions", str(cp), "--out", str(final_out)], capture_output=True, text=True)
        print(rr.stdout[-200:], flush=True)
        if rr.returncode != 0 or not final_out.exists():
            print(f"RECAP FAILED: {rr.stderr[-400:]}", flush=True); final_out = Path(out)
    con.execute("UPDATE cards SET output_video_path=? WHERE card_id=?", (str(final_out.resolve()), card_id))
    con.commit()
    pub = publish_at_for(DATE, SLOT)
    vid = _auto_upload_episode(con, final_out.resolve(), DATE, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED {vid} pub={pub}", flush=True)
    print("DONE_0821_0800", flush=True)
