"""8/18 12:30 제목 교정 — PD 리뷰어가 잡은 실제 escape: live 제목이 옛 '한밤중 정면 대치'(배꼽 theme)인데
내용은 할머니 간식 질투극. (재캡션은 title-theme fix 배포 전이라 stale 제목이 남음.) 간식 질투로 교정.
  run_job.sh scripts/_fix_0818_1230_title.py
"""
import sys
sys.path.insert(0, ".")
from youtube.oauth import get_youtube

VID = "12tPYodXgAk"
NEW_TITLE = "안 먹던 레오, 랴니 주려니까 잽싸게?! 할머니 간식 질투 대소동🧡"
NEW_DESC = ("할머니가 주는 간식을 두 번이나 안 먹고 바닥에 버리던 레오, 랴니한테 주려고 하니까 그제서야 "
            "잽싸게 낚아채 먹네요. 질투쟁이 레오와 삐진 랴니 ♥\n\n#레오랑랴니 #고양이간식 #프렌치불독 #질투 #shorts")

if __name__ == "__main__":
    yt = get_youtube()
    it = yt.videos().list(part="snippet", id=VID).execute().get("items")
    if not it:
        print("NOT FOUND"); sys.exit(1)
    s = it[0]["snippet"]
    print("OLD:", s.get("title"))
    s["title"] = NEW_TITLE
    s["description"] = NEW_DESC
    yt.videos().update(part="snippet", body={"id": VID, "snippet": s}).execute()
    print("NEW:", yt.videos().list(part="snippet", id=VID).execute()["items"][0]["snippet"]["title"])
    print("RETITLED_1230")
