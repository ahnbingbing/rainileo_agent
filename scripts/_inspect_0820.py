"""Inspect the 8/20 08:00 + 21:00 scheduled cards — schema-agnostic dump +
workdir + per-cut durations. Ground truth before PD-directed rework.
  run_job.sh scripts/_inspect_0820.py
"""
import sys, json, glob, subprocess, os
sys.path.insert(0, ".")
from agents.producer import _db

VIDS = {"08:00": "S9kEGX51LGY", "21:00": "oby-SQrHcDg"}
INTEREST = ("card_id", "id", "date", "render_style", "style", "lane", "theme",
            "concept", "title", "output_video_path", "youtube_video_id",
            "youtube_publish_at", "publish_at", "payload", "cuts", "captions",
            "source", "sources", "clip_path")


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
    cur = con.execute("SELECT * FROM cards WHERE youtube_video_id=? "
                      "ORDER BY rowid DESC LIMIT 1", (vid,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        print("NO CARD; columns=", cols, flush=True); return
    d = dict(zip(cols, row))
    cid = d.get("card_id") or d.get("id")
    for k in INTEREST:
        if k in d and d[k] not in (None, ""):
            v = d[k]
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            print(f"  {k}: {s[:900]}", flush=True)
    outp = d.get("output_video_path")
    if outp:
        print("  output exists:", os.path.exists(outp), flush=True)
    cid8 = str(cid).replace("-", "")[:8]
    wds = sorted(glob.glob(f"data/tmp/cameraman_{cid8}_*"))
    print("  workdirs:", wds, flush=True)
    if wds:
        wd = wds[-1]
        for c in sorted(glob.glob(os.path.join(wd, "*.mp4"))):
            print(f"    {os.path.basename(c)}  dur={probe(c)}", flush=True)
        caps = sorted(glob.glob(os.path.join(wd, "*aption*.json")))
        print("    caption jsons:", [os.path.basename(x) for x in caps], flush=True)
        for cj in caps[:2]:
            try:
                print("      ", os.path.basename(cj), "=",
                      json.dumps(json.load(open(cj)), ensure_ascii=False)[:700], flush=True)
            except Exception as e:
                print("      (read err)", e, flush=True)


if __name__ == "__main__":
    con = _db()
    for slot, vid in VIDS.items():
        show(con, slot, vid)
    print("\nDONE_INSPECT", flush=True)
