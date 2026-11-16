"""scripts/mv_tango_C_assemble.py — assemble Tango C (YDKMN) MV from the 13 Seedance cuts.
PD 2026-11-16. Persisted recipe (§8.7). Cut order follows the SONG sections so the phone
killpoint lands on the bridge ("여보세요…전화"). Each 5s Seedance clip is slowed (fill) to its slot.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from scripts.make_mv import make_mv
from scripts.mv_title_card import make_retro_card

ROOT = Path(__file__).resolve().parent.parent
CUTS = ROOT / "data/tmp/mv/tango/cuts_C"
SONG = ROOT / "data/tmp/mv/tango/C_FINAL_song.mp3"        # take 2, 92.4s, faded tail
BUMPER = ROOT / "assets/branding/outro_bumper.mp4"
OUT = ROOT / "data/output/mv/tango_C_ydkmn.mp4"
TITLE_PNG = ROOT / "data/tmp/mv/tango/C_title.png"
# ★타이틀은 곡 스타일에서 자동 선택(에이전트화) — soul 무드 + 영문 스크립트 키커(오마쥬 원곡)
SONG_STYLE = "neo-soul, early-2000s R&B ballad, intimate soulful female diva"

# (cut, slot seconds) in SONG order: diner → party → ★phone(bridge) → warm resolution → ending
PLAN = [
    ("street", 6), ("serving", 7), ("counter", 7), ("leo_longing", 7), ("two_shot", 7),
    ("party_leo", 7), ("party_ryani", 8),
    ("phone_leo", 6), ("phone_ryani", 6),           # ★ killpoint on the bridge
    ("piano_wide", 7), ("paws", 5), ("listening", 8), ("ending", 8),
]


def clean_lines():
    raw = json.load(open(ROOT / "data/tmp/mv/tango/C_karaoke.json", encoding="utf-8"))
    out = []
    for ln in raw:
        t = re.sub(r"\[[^\]]*\]", "", ln["text"])      # drop [Intro]/[Final Chorus]/[End] residue
        t = t.replace("[", "").replace("]", "").strip()
        if not t:
            continue
        out.append({"text": t, "start": ln["start"], "end": ln["end"]})
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true",
                    help="reuse cached concat body (title/caption-only rerun, ~seconds)")
    a = ap.parse_args()
    clips = [{"src": str(CUTS / f"{c}.mp4"), "dur": d, "fill": True} for c, d in PLAN]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 경양식 레트로 EMBLEM 타이틀 (을지로체 + 금색 프레임 + 장식) — PD 2026-11-16
    make_retro_card("내 이름도", TITLE_PNG, kicker="YDKMN",
                    credit="작곡 라레수 · 작사 함미하비", vpos=0.5)
    make_mv(
        song=SONG, clips=clips, lines=clean_lines(), out=OUT, bumper=BUMPER,
        title_image=str(TITLE_PNG),
        bumper_overlap_fade=1.0, fontsize=62, reuse_body=a.fast,
    )
    print(f"TOTAL slot = {sum(d for _c, d in PLAN)}s  (fast={a.fast})")


if __name__ == "__main__":
    main()
