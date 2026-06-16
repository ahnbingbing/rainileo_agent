"""Upload + schedule the two hand-crafted 6/17 RF episodes (cafe @12:30, groom @21:00)."""
import sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from youtube.upload import upload_short
con = sqlite3.connect(ROOT / "data" / "agent.db")

JOBS = [
    dict(card="rf0617cafe000000",
         video="data/output/episodes/episode_rf_cafe_final.mp4",
         title="레오의 첫 카페 나들이 🐾",
         desc="오렌지 고양이 레오의 카페 나들이! 누나 랴니 옆이 명당, 그러다 스르르 꿀잠 😴\n"
              "#고양이 #레오 #랴니 #카페 #catsofyoutube #shorts",
         pub="2026-06-17T03:30:00Z"),
    dict(card="rf0617groom00000",
         video="data/output/episodes/episode_rf_groom_final.mp4",
         title="꼼꼼한 레오의 셀프 관리 타임 🧡",
         desc="그루밍 삼매경 레오 — 앞발 싹, 얼굴 싹, 그러다 발 핥다 말고 스르르 잠드는 중 🐱\n"
              "#고양이 #레오 #그루밍 #cat #catgrooming #shorts",
         pub="2026-06-17T12:00:00Z"),
]
TAGS = ["고양이", "레오", "랴니", "ryani", "leo", "cat", "shorts", "펫", "고양이영상"]

for j in JOBS:
    vp = ROOT / j["video"]
    assert vp.exists(), f"missing {vp}"
    print(f"⬆️  uploading {j['title']} → publishAt {j['pub']} …")
    resp = upload_short(vp, j["title"], j["desc"], tags=TAGS, publish_at_iso=j["pub"])
    vid = resp["id"]
    con.execute(
        "UPDATE cards SET output_video_path=?, youtube_video_id=?, youtube_publish_at=?, "
        "uploaded=1, state='published', date='2026-06-17', updated_at=datetime('now') "
        "WHERE card_id=?",
        (str(vp), vid, j["pub"], j["card"]))
    con.commit()
    print(f"✅ scheduled → https://youtube.com/shorts/{vid}  (공개 {j['pub']})")

con.close()
print("done — 6/17 RF 2슬롯 예약 완료")
