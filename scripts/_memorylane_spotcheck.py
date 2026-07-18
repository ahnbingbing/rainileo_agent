"""Spot-check that the memory-lane temporal-anchor gate held on a batch (retro C13).

For each rendered/published card on the target date, detect whether it's a MULTI-YEAR
memory-lane episode (>=2 cuts whose source clips span >=2 years) and, if so, read the
episode's burned caption manifest (workdir captions.json — the exact source the burn
step used) and verify the OPENER and CLOSER captions still carry a time anchor
(_enforce_memorylane_anchors should have restored them if the caption regen flattened
them). Prints a per-episode PASS/FAIL report; exits 0 always (observability tool).

  .venv/bin/python -m scripts._memorylane_spotcheck [--date YYYY-MM-DD]

Default date = today (KST). Used by the daily post-batch routine and runnable by hand.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "agent.db"
KST = dt.timezone(dt.timedelta(hours=9))

# Keep in lockstep with agents.cameraman._TEMPORAL_MARKER.
_TEMPORAL_MARKER = re.compile(
    r"\d+\s*년\s*전|\d+\s*개월\s*전|\d+\s*달\s*전|\d{4}\s*년|몇\s*해|그때|그\s*시절|어릴|어린\s*시절|"
    r"아기\s*때|아기\s*(랴니|레오)|입양|첫\s*(날|해|낮잠|수영|산책|만남)|지금(은|도)?|여전히|오늘도|"
    r"어느새|이젠|이제는|해가\s*갈수록|해마다|그로부터|"
    r"years?\s*ago|months?\s*ago|back\s*then|as\s*a\s*(pup|puppy|kitten)|these\s*days|"
    r"nowadays|\bstill\b|\btoday\b|first\s*(day|nap|swim|walk)",
    re.IGNORECASE)


def _shoot_date(con, asset_id):
    if not asset_id:
        return None
    try:
        row = con.execute("SELECT captured_iso FROM assets WHERE asset_id=?",
                          (asset_id,)).fetchone()
        if row and row[0]:
            return dt.date.fromisoformat(str(row[0])[:10])
    except Exception:
        pass
    m = re.search(r"(?:^|_)(\d{4})_(\d{2})_(\d{2})_", str(asset_id))
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _has_marker(scenes):
    return any(_TEMPORAL_MARKER.search((sc.get("ko") or "") + " " + (sc.get("en") or ""))
              for sc in (scenes or []))


def _find_captions_json(card_id: str):
    """The workdir captions.json for this card = the burn source (post-gate)."""
    hits = sorted(glob.glob(str(ROOT / "data" / "tmp" / f"cameraman_{card_id[:8]}_*")),
                  reverse=True)
    for wd in hits:
        p = Path(wd) / "captions.json"
        if p.exists():
            return p
    return None


def check_card(con, row) -> dict | None:
    payload = json.loads(row["payload_json"])
    cuts = payload.get("cuts") or []
    live = [c for c in cuts if c.get("function") != "wink_ending"]
    dated = []
    for c in live:
        d0 = _shoot_date(con, c.get("asset_id") or c.get("secondary_asset_id"))
        if d0:
            dated.append((c, d0))
    if len(dated) < 2:
        return None
    yrs = [(dt.date.fromisoformat(row["date"]) - d).days / 365.25 for _, d in dated]
    if max(yrs) < 2 and (max(yrs) - min(yrs)) < 2:
        return None  # not a multi-year memory-lane

    res = {"card_id": row["card_id"][:8], "lane": row["render_style"],
           "title": payload.get("title") or payload.get("theme"),
           "state": row["state"], "video_id": row["youtube_video_id"],
           "span": f"{min(d.year for _, d in dated)}~{max(d.year for _, d in dated)}"}
    cap_path = _find_captions_json(row["card_id"])
    if not cap_path:
        res["verdict"] = "NO_CAPTIONS_JSON"
        return res
    cap = json.loads(cap_path.read_text(encoding="utf-8"))
    tags = [k for k in cap.keys() if not k.startswith("_") and isinstance(cap.get(k), dict)]
    if not tags:
        res["verdict"] = "NO_TAGS"
        return res
    opener, closer = tags[0], tags[-1]
    op_ok = _has_marker(cap[opener].get("scenes"))
    cl_ok = _has_marker(cap[closer].get("scenes"))
    res["opener_anchor"] = op_ok
    res["closer_anchor"] = cl_ok
    res["opener_caps"] = [s.get("ko") for s in cap[opener].get("scenes") or []]
    res["closer_caps"] = [s.get("ko") for s in cap[closer].get("scenes") or []]
    res["verdict"] = "PASS" if (op_ok and cl_ok) else "FAIL"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(KST).date().isoformat())
    a = ap.parse_args()
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT card_id, date, render_style, state, youtube_video_id, payload_json "
        "FROM cards WHERE date=? AND render_style IN ('real_footage','ai_vtuber') "
        "AND state IN ('rendered','published') ORDER BY youtube_publish_at",
        (a.date,)).fetchall()
    found = []
    for row in rows:
        try:
            r = check_card(con, row)
        except Exception as e:
            r = {"card_id": row["card_id"][:8], "verdict": f"ERROR:{e}"}
        if r:
            found.append(r)
    con.close()

    print(f"=== memory-lane spot-check {a.date} — {len(found)} memory-lane episode(s) "
          f"of {len(rows)} rendered ===")
    if not found:
        print("(none — no multi-year memory-lane episode in this batch)")
        return 0
    for r in found:
        print(f"\n[{r.get('verdict')}] {r['card_id']} {r.get('lane','')} "
              f"'{(r.get('title') or '')[:40]}' span={r.get('span','?')} "
              f"state={r.get('state','?')} vid={r.get('video_id')}")
        if "opener_anchor" in r:
            print(f"   opener_anchor={r['opener_anchor']} caps={r['opener_caps']}")
            print(f"   closer_anchor={r['closer_anchor']} caps={r['closer_caps']}")
    fails = [r for r in found if r.get("verdict") == "FAIL"]
    print(f"\nSUMMARY: {len(found)} memory-lane, "
          f"{len(found)-len(fails)} PASS, {len(fails)} FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
