"""Per-cut still 시안 generator for the AV still→Seedance method.

PD rule (2026-06-15): every cut generates **5 candidate stills** and **Giri
picks** the winner (agents/still_select). This runner ties it together for a whole
concept:

  for each cut in the concept JSON:
    1. generate N stills  (scripts/gen_still_multiref.generate: bg + char refs)
    2. Giri picks the winner  (still_select.pick_best_still)
    3. contact sheet w/ winner boxed  (still_select.contact_sheet)
  → write winners + a per-episode contact sheet + summary.json

NO Seedance render here — stills only ($≈0.04/image), held for PD $-OK.

Usage:
  .venv/bin/python -m scripts.gen_cut_stills \\
      --concept /tmp/av_first_swim_concept.json \\
      --outdir data/tmp/first_swim_stills \\
      --bg-map /tmp/first_swim_bg.json \\   # {cut_tag: bg_image_path}  (optional)
      --ref assets/character_ref/ryani_young.png \\
      --n 5
If --bg-map omits a cut (or is absent), falls back to --bg-default.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from scripts import gen_still_multiref
from agents import still_select


def _cut_tag(cut: dict, i: int) -> str:
    return cut.get("tag") or cut.get("beat") or f"cut{i+1}"


def _still_prompt(cut: dict) -> str:
    """Build the FRAME prompt (pose/framing), not the motion prompt.

    NEVER inject caption text into the still prompt — the image model will burn
    the Korean letters into the frame (captions are added later by burn_captions).
    """
    from agents import canon
    p = (cut.get("regen_prompt") or cut.get("regen_direction") or "").strip()
    if not p:
        # Fall back to the cut's scene description as a still brief.
        p = (cut.get("scene") or cut.get("motion_prompt") or "").strip()
    # Hard guards: clean frame (no on-image text) + Ryani marking fidelity.
    p += ("\n\n" + canon.GUARD_NO_TEXT + " " + canon.GUARD_NO_CLOTHING +
          " The dog must read as a small black French Bulldog with only a THIN white "
          "blaze and small white chin/chest/toe markings — NOT a Boston-terrier "
          "white mask or large white chest bib. NO tail. 9:16 vertical, single still frame.")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bg-map", default="", help="JSON {cut_tag: bg_path}")
    ap.add_argument("--bg-default", default="", help="bg used when a cut has no map entry")
    ap.add_argument("--ref", action="append", default=[], help="character ref (repeatable)")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--lane", default="ai_vtuber")
    ap.add_argument("--only", default="", help="comma cut_tags to (re)generate; default all")
    args = ap.parse_args()

    api_key = os.environ["GOOGLE_API_KEY"]
    concept = json.load(open(args.concept))
    cuts = concept.get("cuts") or []
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    bg_map = json.load(open(args.bg_map)) if args.bg_map else {}
    refs = [Path(r) for r in args.ref]
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    con = None
    try:
        con = sqlite3.connect(ROOT / "data" / "agent.db")
    except Exception:
        con = None

    summary = []
    for i, cut in enumerate(cuts):
        tag = _cut_tag(cut, i)
        if only and tag not in only:
            continue
        bg = bg_map.get(tag) or args.bg_default
        if not bg or not Path(bg).exists():
            print(f"[skip {tag}] no bg (map={bg_map.get(tag)!r} default={args.bg_default!r})", flush=True)
            summary.append({"tag": tag, "error": "no bg"})
            continue
        prompt = _still_prompt(cut)
        cands = []
        for k in range(args.n):
            out = outdir / f"{tag}_{k+1}.png"
            try:
                gen_still_multiref.generate(Path(bg), refs, prompt, out, api_key)
                cands.append(out)
                print(f"  [{tag}] still {k+1}/{args.n} ok", flush=True)
            except Exception as e:
                print(f"  [{tag}] still {k+1} FAIL: {e}", flush=True)
        if not cands:
            summary.append({"tag": tag, "error": "all gens failed"})
            continue
        pick = still_select.pick_best_still(cands, cut=cut, concept=concept,
                                            lane=args.lane, con=con)
        win_src = Path(pick["winner_path"])
        win_dst = outdir / f"{tag}_WIN.png"
        win_dst.write_bytes(win_src.read_bytes())
        sheet = outdir / f"_contact_{tag}.png"
        still_select.contact_sheet(cands, sheet, winner=pick["winner"],
                                   label=f"{tag} (Giri #{pick['winner']})")
        print(f"==> {tag}: WIN #{pick['winner']} {win_src.name} :: {pick['reason']}", flush=True)
        summary.append({"tag": tag, "winner": pick["winner"], "winner_file": win_dst.name,
                        "reason": pick["reason"], "candidates": pick["candidates"],
                        "contact_sheet": sheet.name})

    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY:", json.dumps(summary, ensure_ascii=False)[:500], flush=True)
    print("OUTDIR:", outdir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
