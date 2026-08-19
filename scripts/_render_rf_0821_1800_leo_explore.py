"""8/21 18:00 RF 재렌더 (PD 8/21 리뷰: '1800RF는 이미 이전에 나왔던거잖아. 재렌더').
옛 S7RyTzKZMcs='풍덩! 랴니 여름 물가 모험'=물 테마 재탕. PD 지시=신선 '레오 첫 바깥 나들이' footage로 교체.
이 컷=레오의 첫 바깥 탐험, 타일 턱 위를 발끝으로 조심조심 걸으며 검은 호스를 조사(신선·미사용, 08:00 드라이브와 distinct).
단일 클립 + narrator 멀티씬 캡션 recaption_finish 강제 → 18:00 재예약 → 옛 물 영상 veto.
  sudo -u rianileo bash deploy/run_job.sh scripts/_render_rf_0821_1800_leo_explore.py
"""
import sys, json, subprocess, glob, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _render_realfootage_direct, _auto_upload_episode
from agents.launch import publish_at_for

DATE = dt.date(2026, 8, 21)
SLOT = "18:00"
OLD = "S7RyTzKZMcs"  # 물 재탕 — 교체 후 veto
CLIP = "med_2026_08_16_123752_slack_8eb78b3d"  # 레오 야외 타일 턱/검은 호스 탐험 (46.3s, 신선)

concept = {
    "title": "레오의 첫 바깥 탐험 — 타일 턱 위 조심조심",
    "narrative_oneliner": "레오가 용감하게 바깥 탐험을 나섰어요. 낯선 검은 호스를 조사하고, 타일 턱 위를 발끝으로 조심조심 내려옵니다.",
    "subjects": ["leo"], "tone": {"primary": "warm_playful", "intensity": 0.55},
    "bgm_mood": "playful_upbeat", "cuts": [
        {"tag": "c1", "asset_id": CLIP, "trim_start": 1.0, "duration_seconds": 18.0,
         "captions": [{"start": 0.1, "end": 18.0, "ko": "placeholder", "en": "p"}]},
    ],
}

FINAL = {
    "c1": {"scenes": [
        {"start": 0.1, "end": 4.5, "ko": "오늘은 레오, 용감하게 바깥 탐험 나왔어요",
         "en": "Brave little Leo is out exploring today"},
        {"start": 4.5, "end": 9.0, "ko": "요 꼬불꼬불 검은 호스… 정체가 뭐냥? 🐍",
         "en": "This wiggly black hose… what IS it? 🐍"},
        {"start": 9.0, "end": 13.5, "ko": "타일 턱 위를 발끝으로 조심조심",
         "en": "Tip-toeing along the tiled ledge, so careful"},
        {"start": 13.5, "end": 18.0, "ko": "겁 없는 탐험가, 한 발 한 발 내려옵니다",
         "en": "Our fearless explorer climbs down, one paw at a time"}]},
}


def pcb(m):
    print("[rf0821_1800]", m, flush=True)


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
        final_out = Path("data/output/episodes/episode_0821_1800_final.mp4")
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
    if vid and OLD and vid != OLD:
        from youtube.upload import veto_video
        veto_video(OLD, delete=False); print(f"VETOED old {OLD}", flush=True)
    print("DONE_0821_1800", flush=True)
