"""Inspect the 8/20 08:00 (AV?) and 21:00 (RF?) scheduled cards — lane, theme,
source, output path, captions, workdir + per-cut durations.
Ground-truth check before PD-directed rework.
  run_job.sh scripts/_inspect_0820.py
"""
import sys, json, glob, subprocess, os
sys.path.insert(0, ".")
from agents.producer import _db

VIDS = {"08:00": "S9kEGX51LGY", "21:00": "oby-SQrHcDg"}


def probe(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True)
        return round(float(r.stdout.strip()), 2)
    except Exception as e:
        return f"?({e})"


def show(con, slot, vid):
    print(f"\n===== {slot}  {vid} =====", flush=True)
    row = con.execute(
        "SELECT card_id, date, render_style, theme, output_video_path, youtube_video_id, "
        "youtube_publish_at, payload FROM cards WHERE youtube_video_id=? "
        "ORDER BY updated_at DESC LIMIT 1", (vid,)).fetchone()
    if not row:
        print("NO CARD for", vid, flush=True); return
    cid, date, style, theme, outp, yv, pub, payload = row
    print("card_id:", cid, "| date:", date, "| style:", style, flush=True)
    print("theme:", theme, flush=True)
    print("publish_at:", pub, flush=True)
    print("output_video_path:", outp, "| exists:", bool(outp and os.path.exists(outp)), flush=True)
    try:
        p = json.loads(payload) if payload else {}
    except Exception:
        p = {}
    print("payload keys:", list(p.keys()), flush=True)
    for k in ("title", "concept", "source", "sources", "clip", "clip_path",
              "captions", "cuts", "scenes", "caption"):
        if k in p:
            print(f"  {k}: {json.dumps(p[k], ensure_ascii=False)[:700]}", flush=True)
    # locate cameraman workdir
    cid8 = str(cid).replace("-", "")[:8]
    wds = sorted(glob.glob(f"data/tmp/cameraman_{cid8}_*"))
    print("workdirs:", wds, flush=True)
    if wds:
        wd = wds[-1]
        cuts = sorted(glob.glob(os.path.join(wd, "*.mp4")))
        for c in cuts:
            print(f"    {os.path.basename(c)}  dur={probe(c)}", flush=True)
        caps = sorted(glob.glob(os.path.join(wd, "*caption*.json")) +
                      glob.glob(os.path.join(wd, "*captions*.json")))
        print("    caption jsons:", [os.path.basename(x) for x in caps], flush=True)


if __name__ == "__main__":
    con = _db()
    for slot, vid in VIDS.items():
        show(con, slot, vid)
    print("\nDONE_INSPECT", flush=True)
