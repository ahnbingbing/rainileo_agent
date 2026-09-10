"""B4 dry-run — real clip pool → grammar-copy Writer (CAST + grounded COPY) → impact_edit render.
Replaces the hand-authored _phaseb_story_grounded.py: the Writer now casts clips + writes copy.
Pool deliberately mixes a coherent outdoor outing (walks + Ryani swim) with a couple off-setting
indoor clips, to test whether the Writer prefers a coherent thread (principle #2).
  sudo -u rianileo bash deploy/run_job.sh scripts/_phaseb_writer_dryrun.py [story|meme|all]
"""
import sys, os, json, sqlite3
sys.path.insert(0, ".")
from pathlib import Path
from icloud import gcs
import scripts.impact_edit as ie
if os.path.exists("/usr/bin/ffmpeg"):        # system ffmpeg 5.1 supports the arg set (~/.local static drops -vsync)
    ie.FF, ie.FP = "/usr/bin/ffmpeg", "/usr/bin/ffprobe"
from scripts.impact_edit import render_grammar
from agents.edit_grammar_writer import propose_grammar_copy

# candidate pool: coherent outdoor outing + Ryani swim (the payoff) + 2 off-setting indoor clips
POOL_IDS = [
    "med_2026_08_13_123931_slack_330131cb",   # 랴니 수영 (물매니아 payoff)
    "med_2026_08_16_210851_icloud_73873f29",  # 랴니+레오 야외 산책
    "med_2026_08_16_210848_icloud_536eac2a",  # 레오 야외 산책
    "med_2026_08_16_123846_slack_f01079f4",   # 레오 풀숲 산책
    "med_2026_08_16_144614_icloud_79576672",  # 레오 아스팔트 산책
    "med_2026_08_16_123651_slack_cba0d460",   # 레오 야외 산책
    "med_2026_08_16_210743_icloud_f4db7c1c",  # 레오 실내 카페 워킹 (off-setting distractor)
    "med_2026_09_05_002011_slack_904c4f24",   # 실내 거실 둘이 (off-setting distractor)
]


def _pool():
    db = sqlite3.connect("data/agent.db"); db.row_factory = sqlite3.Row
    q = ("SELECT asset_id, file_path, scene_description, activity, subjects_csv, "
         "duration_sec, location_type FROM assets WHERE asset_id=?")
    pool = []
    for aid in POOL_IDS:
        r = db.execute(q, (aid,)).fetchone()
        if r:
            pool.append(dict(r))
    db.close()
    return pool


def _ensure_local(aid, pool):
    fp = next((a["file_path"] for a in pool if a["asset_id"] == aid), None)
    if not fp:
        db = sqlite3.connect("data/agent.db")
        fp = db.execute("SELECT file_path FROM assets WHERE asset_id=?", (aid,)).fetchone()[0]
        db.close()
    lp = gcs.local_path(fp)
    if os.path.exists(lp):
        return
    gcs.download_to(fp)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    grammars = ["story", "meme"] if which == "all" else [which]
    pool = _pool()
    print(f"pool: {len(pool)} clips", flush=True)
    for g in grammars:
        try:
            res = propose_grammar_copy(g, pool)
            print(f"\n=== {g} CAST + COPY ===", flush=True)
            print("clips:", json.dumps(res["clips"], ensure_ascii=False), flush=True)
            print("copy:", json.dumps(res["copy"], ensure_ascii=False)[:900], flush=True)
            for aid in res["clips"].values():
                _ensure_local(aid, pool)
            out = Path(f"data/output/phaseb_b4_{g}.mp4")
            render_grammar(g, out, clips=res["clips"], copy=res["copy"])
            print(f"[render] {g} -> {out} ({'OK' if out.exists() else 'MISSING'})", flush=True)
        except Exception as e:
            import traceback
            print(f"[FAIL] {g}: {e}", flush=True)
            traceback.print_exc()
    print("\nB4_DRYRUN_DONE", flush=True)
