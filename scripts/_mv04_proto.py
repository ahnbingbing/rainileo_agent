#!/usr/bin/env python3
"""Scratch — reference-driven editing prototype (handoff #5).

Validates the conti-Director wiring END TO END on a drive segment WITHOUT touching the shipped
mv04 manifest: run the Director ONCE with the edit_template (saved → reproducible), pick a
contiguous drive beat spanning shot sizes, render its stills with shot-size enforcement + the
scene's location/time token + the mv04 canonical reference, and contact-sheet it. Proves the
stills actually realize WS/MS/CU/ECU variety (not all-medium) within ONE coherent scene.

    python scripts/_mv04_proto.py            # generate cue-sheet (cached) + render segment stills
    python scripts/_mv04_proto.py --contact  # just rebuild the contact sheet from latest stills
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO = ROOT / "data/tmp/mv/mv04proto"
STILLS = PROTO / "stills"


def _cuesheet() -> dict:
    """Director cue-sheet for mv04's concept/lyrics with the edit_template grammar. Cached to
    cuesheet.json so re-runs render from the SAME sheet (LLM is stochastic — fix it once)."""
    cache = PROTO / "cuesheet.json"
    if cache.exists():
        return json.loads(cache.read_text())
    from agents.mv_producer import load_manifest
    from agents.mv_director import direct_cuts
    from agents.mv_ref_director import load_edit_template
    m = load_manifest("mv04")
    cuts, art_direction, scenes = direct_cuts(
        m["concept"]["chosen"], m["song"]["lyrics_data"], m.get("rf_weight", "av_heavy"),
        music_genre=m.get("music_genre", "dance"), n_cuts=26,
        homage=(m.get("homage") or {}).get("doc"),
        edit_template=load_edit_template("mv04"))
    data = {"art_direction": art_direction, "scenes": scenes, "cuts": cuts,
            "canonical_ref": m.get("canonical_ref")}
    PROTO.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def _pick_segment(cuts: list[dict], n: int = 6) -> list[dict]:
    """A contiguous drive beat that spans shot sizes — slide an n-window and take the one with the
    most DISTINCT shot sizes (the best demo of 'varied, not all-medium' within one scene)."""
    av = [c for c in cuts if c.get("cut_type", "av") == "av"]
    best, best_var = av[:n], -1
    for i in range(0, max(1, len(av) - n + 1)):
        win = av[i:i + n]
        var = len({c.get("shot_size") for c in win})
        if var > best_var:
            best, best_var = win, var
    return best


def _render_motion(seg: list[dict]) -> Path | None:
    """i2v each cut's latest still (Seedance FAST) → concat into proto_motion.mp4. The motion test:
    do the tracking/static/orbit cuts hold a constant frame so cut boundaries show NO zoom reset
    (the sawtooth), with only the push-in cut intentionally zooming?"""
    import subprocess
    from scripts.mv_render import render_cut
    clips_dir = PROTO / "cuts"
    mp4s = []
    for c in seg:
        vs = sorted(STILLS.glob(f"{c['tag']}_v*.png"))
        if not vs:
            print("  skip (no still)", c["tag"]); continue
        c["still"] = {"path": str(vs[-1])}
        try:
            mp4 = render_cut(c, clips_dir, seconds=4, fast=True)
            mp4s.append(mp4)
            print("  i2v OK", c["tag"], c.get("camera_move"), "->", Path(mp4).name)
        except Exception as e:  # noqa: BLE001
            print("  i2v FAIL", c["tag"], str(e)[:160])
    if not mp4s:
        return None
    lst = PROTO / "concat.txt"
    lst.write_text("".join(f"file '{m}'\n" for m in mp4s))
    out = PROTO / "proto_motion.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)],
                   check=True, capture_output=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", action="store_true")
    ap.add_argument("--i2v", action="store_true", help="animate the segment stills + concat (motion test)")
    a = ap.parse_args()
    from scripts.mv_still import gen_still, build_contact_sheet

    data = _cuesheet()
    scene_by_id = {s["scene_id"]: s.get("setting") for s in data.get("scenes", []) if s.get("scene_id")}
    seg = _pick_segment(data["cuts"])
    style = data.get("art_direction")
    canonical_ref = data.get("canonical_ref")
    if canonical_ref and not Path(canonical_ref).is_absolute():
        canonical_ref = str(ROOT / canonical_ref)

    print(f"[proto] scene(s): {list(scene_by_id)}")
    print(f"[proto] segment shot sizes: {[c.get('shot_size') for c in seg]}")
    print(f"[proto] camera moves:       {[c.get('camera_move') for c in seg]}")

    if not a.contact:
        for c in seg:
            try:
                p = gen_still(c, STILLS, style=style, canonical_ref=canonical_ref,
                              scene_setting=scene_by_id.get(c.get("scene_id")))
                print("  OK", c["tag"], c.get("shot_size"), "->", p.name)
            except Exception as e:  # noqa: BLE001
                print("  FAIL", c["tag"], str(e)[:160])

    latest = []
    for c in seg:
        vs = sorted(STILLS.glob(f"{c['tag']}_v*.png"))
        if vs:
            latest.append(vs[-1])
    if latest:
        sheet = build_contact_sheet(latest, PROTO / "proto_contact.png", cols=3)
        print("[proto] contact sheet →", sheet)

    if a.i2v:
        print("[proto] i2v motion test (Seedance FAST)…")
        mv = _render_motion(seg)
        if mv:
            print("[proto] motion →", mv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
