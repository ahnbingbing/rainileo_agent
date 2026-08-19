"""8/21 18:00 RF 재렌더 (PD 8/21 리뷰: '1800RF는 이미 이전에 나왔던거잖아. 재렌더').
옛 S7RyTzKZMcs='풍덩! 랴니 여름 물가 모험'=물 재탕. PD 지시=신선 '레오 첫 바깥 나들이' footage·물 제외.
v1(타일턱 클립)은 실제 주된 내용이 '물 마시기'라 Giri 제목-내용 불일치+물 인접 → 카페 클립으로 교체.
이 컷=레오의 첫 카페 나들이, 러그 위를 걸으며 의자 다리를 킁킁 탐색(물 없음, 08:00 드라이브와 distinct).
단일 클립 + narrator 멀티씬 캡션 → 18:00 재예약 → v1(HQKTgNTpo5A) veto.
  sudo -u rianileo bash deploy/run_job.sh scripts/_render_rf_0821_1800_leo_explore.py
"""
import sys, json, subprocess, glob, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _render_realfootage_direct, _auto_upload_episode
from agents.launch import publish_at_for

DATE = dt.date(2026, 8, 21)
SLOT = "18:00"
OLD = "HQKTgNTpo5A"  # v1(타일턱/물마시기) — 교체 후 veto  (원래 물영상 S7RyTzKZMcs는 이미 veto됨)
CLIP = "med_2026_08_16_123839_slack_0fbcbef8"  # 레오 카페 러그 위 탐색 (30.2s, 신선, 물 없음)

concept = {
    "title": "레오의 첫 카페 나들이 — 여긴 어디냥?",
    "narrative_oneliner": "레오가 처음으로 카페에 왔어요. 러그 위를 조심조심 걸으며 의자 다리며 낯선 냄새를 킁킁 탐색합니다.",
    "subjects": ["leo"], "tone": {"primary": "warm_playful", "intensity": 0.55},
    "bgm_mood": "playful_upbeat", "cuts": [
        {"tag": "c1", "asset_id": CLIP, "trim_start": 1.0, "duration_seconds": 17.0,
         "captions": [{"start": 0.1, "end": 17.0, "ko": "placeholder", "en": "p"}]},
    ],
}

FINAL = {
    "c1": {"scenes": [
        {"start": 0.1, "end": 4.5, "ko": "레오, 태어나서 첫 카페 나들이 왔어요 ☕",
         "en": "Leo's very first café outing ☕"},
        {"start": 4.5, "end": 9.0, "ko": "폭신한 러그를 밟으며 한 걸음 한 걸음",
         "en": "Padding across the fluffy rug, step by step"},
        {"start": 9.0, "end": 13.0, "ko": "의자 다리는… 킁킁, 이건 무슨 냄새냥?",
         "en": "Sniff sniff—what's this smell by the chair leg?"},
        {"start": 13.0, "end": 17.0, "ko": "낯선 곳도 씩씩하게 탐험하는 우리 막내 🐾",
         "en": "Our brave little one explores every new place 🐾"}]},
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
