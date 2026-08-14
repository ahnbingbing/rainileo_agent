"""8/15 08:00 AV 재렌더 — 광복절: 레오·랴니가 대문 앞에 태극기를 게양.
PD: (1) 이전 버전은 태극기가 아예 안 나옴 → Seedance는 텍스트로 국기를 못 그린다.
    → assets/props/taegeukgi.png(직접 그린 태극 도안)을 **--mode ref 레퍼런스로 주입**해
      Seedance가 실제 국기 도안을 보고 렌더하게 한다.
(2) 이전 버전은 랴니가 cut1엔 마킹 없다가 마지막엔 꼬리까지 생김(캐논 위반).
    → 전 컷 ref 모드(체인 없음) + 매 프롬프트에 '꼬리 없음·마킹 고정' 명시 + 캐릭터 ref(official/ryani_solo) 주입.
5컷을 각각 ref 렌더 → workdir 조립(recaption_finish, 범퍼+BGM) → 8/15 08:00 예약 → 옛 bW9xaR78bJo veto.
Run(VM): sudo -u rianileo bash deploy/run_job.sh scripts/_render_av_0815_gwangbok_flag.py
"""
import sys, json, subprocess, os, datetime as dt
sys.path.insert(0, ".")
from pathlib import Path
from agents.producer import _db, _auto_upload_episode
from agents.launch import publish_at_for

TARGET = dt.date(2026, 8, 15)
SLOT = "08:00"
OLD_VID = "bW9xaR78bJo"
WD = Path("data/tmp/gwangbok_0815_flag")
MODEL = os.getenv("SEEDANCE_MODEL", "dreamina-seedance-2-0-fast-260128")

REF_PAIR = "assets/character_ref/official_ryani_leo.png"
REF_RYANI = "assets/character_ref/ryani_solo.png"
REF_FLAG = "assets/props/taegeukgi.png"

# Shared scene grounding injected into every cut prompt (ref mode has no first_frame,
# so each prompt must fully describe the scene + lock the characters + the flag).
BASE = (
    "Photoreal home video, realistic look (NOT illustration or cartoon). Setting: the front gate / "
    "doorway of a Korean house, warm morning light. EXACTLY ONE orange tabby cat (Leo — orange tabby, "
    "white chin, pale yellow-green eyes) and EXACTLY ONE small black French Bulldog (Ryani — a THIN white "
    "pencil-line blaze up the muzzle, white chin, a large white chest patch, bat ears, silver-grey muzzle, "
    "and absolutely NO TAIL). Ryani's white markings and her tail-less rear stay EXACTLY the same in every "
    "moment and never change, appear, disappear, grow, or drift. Never render a tail on the dog. "
    "A South Korean national flag (Taegeukgi — white field, a red-over-blue taegeuk circle in the centre, "
    "four black trigrams at the corners, EXACTLY like the provided flag reference image) is hoisted on a "
    "pole beside the gate and waves gently in the breeze. Camera is completely fixed; the background, gate "
    "and pole are static; the only movement is the flag waving softly plus the one pet action described. "
)

CUTS = [
    ("cut1_intro", 5,
     BASE + "The cat and the tail-less dog stand together at the gate and lift their heads to look up at "
            "the waving Taegeukgi with curious, proud faces."),
    ("cut2_raise", 5,
     BASE + "The orange tabby cat lifts a front paw toward the flag pole as if helping raise the flag, "
            "while the flag rises and unfurls fully; the tail-less dog watches proudly beside it."),
    ("cut3_manse", 5,
     BASE + "The cat and the tail-less dog both raise their front paws up high in a cheerful 'manse' "
            "(hurray) gesture beneath the fully-waving Taegeukgi, mouths a little open in celebration."),
    ("cut4_salute", 5,
     BASE + "The cat and the tail-less dog sit neatly side by side facing the waving flag, chests out, in "
            "a proud, dignified pose as if saluting the Taegeukgi."),
    ("cut5_wink_ending", 5,
     BASE + "The two sit together under the flag. THE ONLY EXTRA MOTION: the orange tabby cat looks at the "
            "camera and slowly closes ONE eye in a clear deliberate WINK for a beat, then reopens it with a "
            "tiny smile. The tail-less dog holds still; her markings do not change."),
]

CAPTIONS = {
    "cut1_intro":        {"ko": "오늘은 무슨 날이냥?",        "en": "What day is it today?"},
    "cut2_raise":        {"ko": "태극기 다는 날이지!",        "en": "It's flag-raising day!"},
    "cut3_manse":        {"ko": "대~한독립 만세냥!",          "en": "Hurray for Korea!"},
    "cut4_salute":       {"ko": "우리도 광복절 지킴이냥",      "en": "We guard Liberation Day too"},
    "cut5_wink_ending":  {"ko": "광복절 잘 보내라냥 ♥",       "en": "Happy Liberation Day ♥"},
}
BGM = "assets/bgm/cocosmusic-funshine-groove-281923.mp3"


def pcb(m):
    print("[gwangbok]", m, flush=True)


def render_cut(tag, secs, prompt):
    out = WD / "animated" / f"{tag}.mp4"
    for attempt in range(2):
        r = subprocess.run(
            [sys.executable, "scripts/animate_seedance_i2v.py", "--mode", "ref",
             "--ref-image", REF_PAIR, "--ref-image", REF_RYANI, "--ref-image", REF_FLAG,
             "--prompt", prompt, "--seconds", str(secs), "--model", MODEL,
             "--output", str(out)],
            capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            pcb(f"{tag} rendered ({secs}s)")
            return True
        pcb(f"{tag} attempt {attempt+1} FAILED rc={r.returncode} exists={out.exists()}")
        pcb(f"  STDERR: {r.stderr[-1500:]}")
        pcb(f"  STDOUT: {r.stdout[-600:]}")
    return False


if __name__ == "__main__":
    (WD / "animated").mkdir(parents=True, exist_ok=True)
    if not Path(REF_FLAG).exists():
        print("MISSING FLAG REF:", REF_FLAG, flush=True); sys.exit(1)
    for tag, secs, prompt in CUTS:
        if not render_cut(tag, secs, prompt):
            print(f"RENDER FAILED at {tag}", flush=True); sys.exit(1)
    # captions.json (recaption_finish format: tag -> {scenes:[...]}) + native tempo
    caps = {}
    for tag, secs, _ in CUTS:
        c = CAPTIONS[tag]
        caps[tag] = {"scenes": [{"start": 0.0, "end": float(secs), "ko": c["ko"], "en": c["en"]}]}
    caps["_tempo_factors"] = {tag: 1.0 for tag, _, _ in CUTS}
    (WD / "captions.json").write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
    (WD / "render_meta.json").write_text(json.dumps({"bgm": BGM}), encoding="utf-8")
    out = "data/output/episodes/episode_av_0815_gwangbok_flag.mp4"
    rr = subprocess.run([sys.executable, "-m", "scripts.recaption_finish", "--workdir", str(WD),
                         "--captions", str(WD / "captions.json"), "--out", out, "--bgm", BGM],
                        capture_output=True, text=True)
    print(rr.stdout[-500:], flush=True)
    if rr.returncode != 0 or not Path(out).exists():
        print("ASSEMBLE FAILED:\n", rr.stderr[-1200:], flush=True); sys.exit(1)
    con = _db()
    pub = publish_at_for(TARGET, SLOT)
    vid = _auto_upload_episode(con, Path(out).resolve(), TARGET, progress_cb=pcb, publish_at_iso=pub)
    print(f"SCHEDULED new video_id={vid} publish_at={pub}", flush=True)
    if vid and vid != OLD_VID:
        from youtube.upload import veto_video
        veto_video(OLD_VID, delete=False)
        print(f"VETOED old {OLD_VID}", flush=True)
    print("DONE", flush=True)
