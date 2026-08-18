"""8/20 21:00 RF 재캡션 + 예약 — 어린 랴니(2019 pre-Leo) 강가 데크 메모리레인.
PD: (1) '2100R는 어린 랴니가! 라고 해야지' — pre-Leo 어린 랴니로 프레이밍.
    (2) '왜 캡션이 하나로 끝이야? 장난해?' — salvage 경로가 원래 멀티비트 컨셉 캡션을
        단일 generic('랴니가 벤치에 앉아서…')으로 뭉갬 → 멀티씬 narrator 복원.
소스=med_2019_11_24(늦가을, 어린 랴니 ~4살, 레오 출생 2025-09 이전=pre-Leo). 프레임확인:
어린 랴니(꼬리없음·흰 가슴/턱), 보라색 하네스+회색 리드줄, 어두운 나무 데크에 앉아 물 건너
마른 갈대밭을 경계하듯 고개 돌려가며 살핌. wistful dongmulnongjang 톤.
  sudo -u rianileo bash deploy/run_job.sh scripts/_recap_0820_2100.py
"""
import sys, json, subprocess, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _auto_upload_episode
from agents.launch import publish_at_for

CARD = "48250b09-96a3-4030-a3c2-10ac56d3b1ca"
WD = Path("data/tmp/cameraman_48250b09_20260818_063726")
DATE = dt.date(2026, 8, 20)
SLOT = "21:00"
OLD_VID = "oby-SQrHcDg"
OUT = Path("data/output/episodes/episode_rf_0820_2100_recap.mp4")

# cut1_intro = 22.5s single cut → 5 grounded beats (어린 랴니 pre-Leo memory-lane)
CAPS = {
    "cut1_intro": {"scenes": [
        {"start": 0.1, "end": 4.6,
         "ko": "어린 랴니가 강가 데크에 앉았어요— 막내 레오도 없던 6년 전 늦가을",
         "en": "Little Ryani on the riverside deck—6 autumns ago, before baby Leo existed"},
        {"start": 4.6, "end": 9.2,
         "ko": "보라색 하네스 야무지게 매고, 오늘은 엄마랑 강가 산책!",
         "en": "Snug in her purple harness—a riverside walk with mom today!"},
        {"start": 9.2, "end": 13.8,
         "ko": "물 건너 갈대밭에서 부스럭… 귀 쫑긋, 시선 딱 고정!",
         "en": "A rustle from the reeds across the water—ears up, eyes locked!"},
        {"start": 13.8, "end": 18.2,
         "ko": "고개 이리저리, 강 구경에 폭 빠진 어린 랴니",
         "en": "Head turning this way and that—young Ryani, lost in the river view"},
        {"start": 18.2, "end": 22.4,
         "ko": "그땐 몰랐죠— 몇 년 뒤, 개구쟁이 동생이 찾아올 줄은 ♥",
         "en": "She had no idea—years later, a mischievous little brother would arrive ♥"},
    ]},
    "_tempo_factors": {"cut1_intro": 1.0},
}


def pcb(m):
    print("[recap0820_2100]", m, flush=True)


if __name__ == "__main__":
    if not WD.exists():
        print("NO WORKDIR", WD, flush=True); sys.exit(1)
    caps_path = WD / "recap_0820_2100_caps.json"
    caps_path.write_text(json.dumps(CAPS, ensure_ascii=False, indent=2), encoding="utf-8")
    rr = subprocess.run([sys.executable, "-m", "scripts.recaption_finish", "--workdir", str(WD),
                         "--captions", str(caps_path), "--out", str(OUT)],
                        capture_output=True, text=True)
    print(rr.stdout[-600:], flush=True)
    if rr.returncode != 0 or not OUT.exists():
        print("RECAP FAILED:\n", rr.stderr[-1500:], flush=True); sys.exit(1)
    con = _db()
    con.execute("UPDATE cards SET output_video_path=? WHERE card_id=?", (str(OUT.resolve()), CARD))
    con.commit()
    pub = publish_at_for(DATE, SLOT)
    vid = _auto_upload_episode(con, OUT.resolve(), DATE, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED new video_id={vid} publish_at={pub}", flush=True)
    if vid and vid != OLD_VID:
        from youtube.upload import veto_video
        veto_video(OLD_VID, delete=False)
        print(f"VETOED old {OLD_VID}", flush=True)
    print("RECAP0820_2100_DONE", flush=True)
