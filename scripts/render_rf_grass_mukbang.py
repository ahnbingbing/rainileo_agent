"""Render the PD-directed RF episode '레오 풀먹방' — montage of the 2026-05-05 cat-grass
mukbang. No upload — render + Giri only, deliver mp4 for PD review.

The normal pool dropped the 46s main mukbang clip (40 days old, beyond the recent
window), so the first attempt made a thin 2-cut episode missing the real mukbang.
Fix: CONSTRAIN context.available_videos to exactly the grass/flop clips so the Writer
must build the episode from them. Montage forced (eat → close-up → flop-on-Ryani kick),
since same-clip slicing is disabled. Directive sets the warm+wit 풀먹방 tone (exercises
the new caption-tone prompts).
"""
import datetime as dt
import os
import sqlite3
import unicodedata as ud
from agents.producer import (_db, _gather_context, propose_concepts,
                             _render_realfootage_direct)
from agents import arc

TARGET = dt.date(2026, 6, 16)
DIRECTIVE = (
    "레오의 풀먹방 (real_footage 몽타주). 2026-05-05 거실 소파에서 레오가 금속 그릇/트레이의 "
    "고양이 풀(캣그래스)을 진지하게 우적우적 먹는 게 메인. 진지한 먹방 표정과 리듬을 따뜻+위트 "
    "vlog 톤으로. 먹고 난 뒤 레오가 랴니 등 위에 폴짝 올라가 기대 쉬는 컷이 킥/여운. 산책 아님."
)
# the exact mukbang clips, in narrative order
WANT = [
    "med_2026_05_05_124056_icloud_a4485f9f",  # 46s — Leo eating cat grass from tray (MAIN)
    "med_2026_05_05_124151_icloud_1dd62157",  # 4.6s — close-up eating
    "med_2026_05_05_130324_icloud_7ba59b4c",  # 13s — Leo flops onto Ryani's back (KICK)
]


def pcb(m):
    print("P:", m, flush=True)


def _video_entry(con, aid):
    cur = con.execute(
        "SELECT duration_sec, has_human, scene_description, activity, subjects_csv, "
        "captured_iso, location_type, mood FROM assets WHERE asset_id=?", (aid,))
    r = cur.fetchone()
    dur, hh, sc, act, subs, iso, loc, mood = r
    N = lambda s: ud.normalize("NFC", s or "")
    return {
        "id": aid, "act": act or "", "sub": N(subs) or "leo",
        "mood": mood or "warm", "sc": N(sc), "dur": float(dur or 0),
        "date": (iso or "2026-05-05")[:10], "loc": loc or "home_living",
        "has_human": hh or 0, "both": ("ryani" in N(subs) and "leo" in N(subs)),
        "years_ago": 0,
    }


def main():
    con = _db()
    print("gathering context...", flush=True)
    context = _gather_context(con, TARGET)
    # CONSTRAIN the pool to the mukbang clips only
    context["available_videos"] = [_video_entry(con, a) for a in WANT]
    context["archive_videos"] = []
    context["exclude_asset_ids"] = []
    context["best_photos"] = []        # video-only: no photo padding
    context["archive_photos"] = []
    for v in context["available_videos"]:
        print("  pool:", v["id"], "dur", round(v["dur"], 1), "sc", v["sc"][:60], flush=True)
    arc.set_concept_directive(con, TARGET.isoformat(), DIRECTIVE)
    os.environ["RF_FORCE_ONETAKE"] = "never"   # force montage across the distinct clips
    os.environ["RF_ONETAKE_COOLDOWN"] = "0"
    os.environ["REVIEWER_MAX_REWRITES"] = "0"   # PD-directed concept: don't let freshness gate loop
    try:
        print("proposing real_footage concept (constrained pool)...", flush=True)
        concepts = propose_concepts(TARGET, context, style_filter="real_footage",
                                    progress_cb=pcb)
        if not concepts:
            print("NO CONCEPT PROPOSED", flush=True)
            return
        c = concepts[0]
        title = c.get("title")
        title = title.get("ko") if isinstance(title, dict) else title
        print("CONCEPT:", title, flush=True)
        for i, cut in enumerate(c.get("cuts") or []):
            caps = cut.get("captions") or []
            print(f"  cut{i+1} asset={cut.get('asset_id')} dur={cut.get('duration_seconds')}", flush=True)
            for s in caps:
                print(f"      cap: {s.get('ko')}", flush=True)
        print("rendering (no upload)...", flush=True)
        out, report, card_id = _render_realfootage_direct(c, TARGET, con, progress_cb=pcb)
        print("OUT:", out, flush=True)
        print("CARD_ID:", card_id, flush=True)
        if report:
            print("GIRI 판정:", report.get("판정"), "점수:", report.get("점수"), flush=True)
    finally:
        con.execute("DELETE FROM pd_concept_directives WHERE target_date=? AND directive=?",
                    (TARGET.isoformat(), DIRECTIVE))
        con.commit()
        print("directive cleaned up", flush=True)


if __name__ == "__main__":
    main()
