"""One-off: schedule the Halloween MV public at 13:00 KST today (empty slot, before the 18/21 band)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from youtube.upload import upload_short

TITLE = "박쥐 된 댕냥이 🦇 오늘 밤 주인공 (랴니레오 할로윈 MV)"
DESC = (
    "할로윈 맞이 랴니레오 오리지널 뮤직비디오 🦇🎃\n"
    "박쥐 날개 달고… 오늘 밤 주인공은 우리!\n"
    "작곡 라레수 · 작사 함미하비\n\n"
    "#할로윈 #랴니레오 #고양이 #강아지 #프렌치불독 #할로윈코스튬 #오리지널곡 #shorts"
)
VIDEO = "data/tmp/mv/topics/랴니레오_할로윈MV_오늘밤주인공.mp4"
PUBLISH_AT = "2026-11-04T04:00:00Z"   # 13:00 KST

out = upload_short(VIDEO, TITLE, DESC, publish_at_iso=PUBLISH_AT)
print("VIDEO_ID", out["id"])
print("WATCH https://youtu.be/" + out["id"])
