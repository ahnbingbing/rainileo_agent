"""8/21 21:00 AV 캡션 다듬기 (PD "기리가 맞아 — 캡션 다듬어").
Giri 지적: cut1 '첫 모델 누굴까'가 둘 다 이미 무대에 있어 어긋남 + 엔딩 윙크 약함.
→ cut1 scene2='스포트라이트 켜지고 쇼 스타트', cut5='심사위원 만장일치 오늘의 스타는?'→윙크 '오늘도 햅삐'.
recaption_finish로 재조립 → 21:00 재예약 → 옛 dgxZ1-_bsyY veto. 캡션만 교체(재렌더 아님).
  sudo -u rianileo bash deploy/run_job.sh scripts/_recap_av_0821_2100.py
"""
import os, sys, glob, subprocess, datetime as dt
os.environ["PATH"] = "/home/rianileo/.local/bin:" + os.environ.get("PATH", "")
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _auto_upload_episode
from agents.launch import publish_at_for

DATE = dt.date(2026, 8, 21); SLOT = "21:00"
OLD = "dgxZ1-_bsyY"
CARD = "5f6504c9"
CAPS = "/tmp/av2100_caps.json"


def pcb(m):
    print("[recap2100]", m, flush=True)


if __name__ == "__main__":
    wds = sorted(glob.glob(f"data/tmp/cameraman_{CARD}_*"), reverse=True)
    if not wds:
        print("NO WORKDIR", flush=True); sys.exit(1)
    wd = wds[0]
    out = "data/output/episodes/episode_0821_2100_recap.mp4"
    rr = subprocess.run([sys.executable, "-m", "scripts.recaption_finish",
                         "--workdir", wd, "--captions", CAPS, "--out", out],
                        capture_output=True, text=True)
    print(rr.stdout[-600:], flush=True)
    if rr.returncode != 0 or not Path(out).exists():
        print(f"RECAP FAILED: {rr.stderr[-600:]}", flush=True); sys.exit(1)
    con = _db()
    con.execute("UPDATE cards SET output_video_path=? WHERE card_id LIKE ?", (str(Path(out).resolve()), CARD + "%"))
    con.commit()
    pub = publish_at_for(DATE, SLOT)
    vid = _auto_upload_episode(con, Path(out).resolve(), DATE, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED {vid} pub={pub}", flush=True)
    if vid and vid != OLD:
        from youtube.upload import veto_video
        veto_video(OLD, delete=False); print(f"VETOED old {OLD}", flush=True)
    print("DONE_RECAP_2100", flush=True)
