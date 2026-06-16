"""PD 2026-06-17 (트러스트 빌드): hand-craft TWO RF cards for 6/17 from PD-chosen
real footage — a Leo cafe outing (same-day, 3 video cuts) + a Leo grooming long-take.
Footage-first, fun captions (no 도사 tone), Leo prominent. Renders via cameraman
--no-brain with grounding/punch-up OFF so MY captions ship verbatim."""
import json, sqlite3, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
con = sqlite3.connect(ROOT / "data" / "agent.db")

# 1) register the pre-trimmed grooming clip as a new asset (trim baked in → trim_start 0)
GROOM_TRIM = "med_2026_06_11_123101_groomtrim"
con.execute(
    "INSERT OR REPLACE INTO assets (asset_id, source, kind, file_path, ingested_iso, "
    "duration_sec, location_type, subjects_csv, has_human, activity, scene_description, "
    "vlm_analyzed_at) VALUES (?,?,?,?,datetime('now'),?,?,?,?,?,?,datetime('now'))",
    (GROOM_TRIM, "icloud", "video",
     "data/assets/clips/2026/med_2026_06_11_123101_groomtrim.mp4", 22.1,
     "home", "leo", 0, "grooming",
     "레오가 둥근 펫 베드 안에서 앞발과 얼굴을 핥으며 그루밍하다 졸려 하는 클로즈업 롱테이크."))

def aid(pref):
    r = con.execute("SELECT asset_id FROM assets WHERE asset_id LIKE ?||'%'", (pref,)).fetchone()
    return r[0] if r else None

CAFE_1 = aid("med_2026_06_13_125601")   # Leo exploring the cafe ledge
CAFE_2 = aid("med_2026_06_13_143905")   # Leo + Ryani together
CAFE_3 = aid("med_2026_06_13_133354")   # Leo napping

def cut(beat, who, space, asset_id, dur, action, caps):
    return {"beat": beat, "who": who, "space": space, "asset_id": asset_id,
            "duration_seconds": dur, "edit_effect": "static", "action": action,
            "captions": [{"start": s, "end": e, "ko": k, "en": en} for (s, e, k, en) in caps]}

cafe_cuts = [
    cut("intro", "leo", "cafe", CAFE_1, 9.0,
        "레오가 하네스를 차고 카페 안 낮은 턱을 따라 걸으며 유리문 너머 바깥을 살핀다.",
        [(0.5, 4.7, "레오, 인생 첫 카페 출동!", "Leo's first-ever cafe trip!"),
         (4.7, 9.0, "일단 구석구석 정찰부터", "First, a full recon lap")]),
    cut("develop", "leo", "cafe", CAFE_2, 9.0,
        "레오가 카페 나무 바닥에 앉아 있고 옆에 랴니가 함께 있다.",
        [(0.5, 4.5, "누나 옆이 명당이지", "Best seat? Next to sis"),
         (4.5, 9.0, "이 구역, 우리가 접수", "This spot's ours now")]),
    cut("closer", "leo", "cafe", CAFE_3, 10.0,
        "레오가 카페 바닥에 웅크리고 잠들어 있다.",
        [(0.5, 4.5, "탐험가도 카페 식곤증", "Even explorers get sleepy"),
         (4.5, 10.0, "정복 완료, 이제 꿀잠", "Conquered. Nap time now")]),
]

groom_cuts = [
    cut("intro", "leo", "home", GROOM_TRIM, 22.0,
        "레오가 둥근 펫 베드에서 앞발과 얼굴을 핥으며 꼼꼼히 그루밍하다 점점 졸려 한다.",
        [(0.5, 3.5, "셀프 관리 시간 시작", "Self-care hour begins"),
         (3.5, 6.5, "앞발 싹— 얼굴 싹—", "Paws scrub, face scrub"),
         (6.5, 9.5, "여기도 저기도 꼼꼼", "Thorough, every spot"),
         (9.5, 12.5, "한 곳도 안 빼놓고…", "Never misses a bit…"),
         (12.5, 15.5, "근데 슬슬 졸리네?", "But… getting sleepy?"),
         (15.5, 18.5, "발 핥다 말고 스르르", "Mid-lick, drifting off"),
         (18.5, 22.0, "잘 자라, 우리 레오", "Sweet dreams, Leo")]),
]

now = dt.datetime.utcnow().isoformat()
def make_card(cid, title, cuts):
    payload = {"card_id": cid, "created_at": now, "author": "pd_trust",
               "card_type": "episode", "date": "2026-06-17", "theme": title,
               "title": title, "render_style": "real_footage",
               "episode_format": "short", "tone": "playful",
               "subjects": "leo", "duration_target_sec": sum(c["duration_seconds"] for c in cuts),
               "cuts": cuts}
    con.execute(
        "INSERT OR REPLACE INTO cards (card_id, date, created_at, author, card_type, "
        "theme, tone_primary, ask_pd, state, payload_json, updated_at, render_style, uploaded) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),?,0)",
        (cid, "2026-06-17", now, "pd_trust", "daily", title, "playful", 0, "approved",
         json.dumps(payload, ensure_ascii=False), "real_footage"))
    print(f"card {cid} → {title} ({len(cuts)} cuts)")

make_card("rf0617cafe000000", "레오의 첫 카페 탐방기", cafe_cuts)
make_card("rf0617groom00000", "레오의 셀프 관리 시간", groom_cuts)
con.commit(); con.close()
print("done")
