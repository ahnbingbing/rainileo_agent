"""8/15 08:00 광복절 AV — 렌더된 mp4를 슬롯 카드에 연결하고 예약 마무리.
렌더/조립은 됐으나 output 경로가 카드에 안 붙어 _auto_upload가 ORPHAN-SKIP → 여기서 카드
output_video_path를 갱신하고(현 08:00 슬롯 = bW9xaR78bJo 카드 재활용) 재예약 + 옛 영상 veto.
Run(VM): sudo -u rianileo bash deploy/run_job.sh scripts/_finish_0815_gwangbok.py
"""
import sys, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _auto_upload_episode
from agents.launch import publish_at_for

OLD_VID = "bW9xaR78bJo"
TARGET = dt.date(2026, 8, 15)
SLOT = "08:00"
OUT = Path("data/output/episodes/episode_av_0815_gwangbok_flag.mp4").resolve()


def pcb(m):
    print("[fin]", m, flush=True)


if __name__ == "__main__":
    if not OUT.exists():
        print("MISSING OUT:", OUT, flush=True); sys.exit(1)
    con = _db()
    row = con.execute("SELECT card_id FROM cards WHERE youtube_video_id=?", (OLD_VID,)).fetchone()
    if not row:
        row = con.execute("SELECT card_id FROM cards WHERE date=? AND render_style='ai_vtuber' "
                          "ORDER BY updated_at DESC LIMIT 1", (TARGET.isoformat(),)).fetchone()
    if not row:
        print("NO CARD to repoint", flush=True); sys.exit(1)
    card_id = row[0]
    con.execute("UPDATE cards SET output_video_path=?, youtube_video_id=NULL, "
                "youtube_publish_at=NULL, uploaded=0 WHERE card_id=?", (str(OUT), card_id))
    con.commit()
    print(f"repointed card {card_id} -> {OUT}", flush=True)
    pub = publish_at_for(TARGET, SLOT)
    vid = _auto_upload_episode(con, OUT, TARGET, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED new video_id={vid} publish_at={pub}", flush=True)
    if vid and vid != OLD_VID:
        from youtube.upload import veto_video
        veto_video(OLD_VID, delete=False)
        print(f"VETOED old {OLD_VID}", flush=True)
    print("FIN_DONE", flush=True)
