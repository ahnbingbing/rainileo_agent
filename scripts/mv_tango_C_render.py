"""scripts/mv_tango_C_render.py — PERSISTED Seedance render driver for Tango C (YDKMN).
PD 2026-11-16. Each cut: locked still (refs/C_av/) → Seedance FAST i2v (5s) with the motion
prompt from notes/mv_tango_C_motion.md + an explicit CHARACTER LOCK appended (PD: "드리프트 조심").

FAST i2v = 5s clips only; final per-cut length is set at assembly (make_mv `fill`), killpoints longest.

Usage:
  python scripts/mv_tango_C_render.py --list
  python scripts/mv_tango_C_render.py --only phone_leo,phone_ryani   # killpoints first
  python scripts/mv_tango_C_render.py --all
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAV = ROOT / "data/tmp/mv/tango/refs/C_av"
OUTDIR = ROOT / "data/tmp/mv/tango/cuts_C"
SEEDANCE = ROOT / "scripts/animate_seedance_i2v.py"

# ── character locks (appended to every prompt to fight i2v drift) ──
LOCK_R = ("The dog stays a black French bulldog with NO TAIL, bat ears, a flat brachycephalic face and "
          "her white chest/chin/muzzle-blaze markings — keep these exact, never grow a tail.")
LOCK_L = ("The cat stays an orange tabby with his stripes, pink nose and pale eyes — keep his markings "
          "and proportions exact.")

# cut -> (still, motion prompt, character lock). Order = song order.
CUTS = {
    "street":       (CAV / "sc1_street_v2.png",
                     "The camera glides slowly in down the wet cobblestone street toward the glowing bar; "
                     "fine rain falls, the hanging bar sign sways, the neon flickers, a puddle ripples.", ""),
    "serving":      (CAV / "ryani_serving_v3.png",
                     "Gentle drift-in. The small no-tail French bulldog sits on the diner stool; she tilts "
                     "her head to one side, then her ears perk up, then a slow blink; steam curls off the "
                     "plate beside her.", LOCK_R),
    "counter":      (CAV / "sc2_counter_v4.png",
                     "Slow push-in across the counter. The orange tabby cat turns his eyes sideways toward "
                     "her, whiskers twitch forward, his tail-tip flicks once; behind the counter the no-tail "
                     "French bulldog perks her ears and gives a small shoulder shrug.", LOCK_R + " " + LOCK_L),
    "leo_longing":  (CAV / "sc3_leo_longing_v4.png",
                     "Slow creep-in to a tight close-up. The orange tabby cat holds a wistful look, gives one "
                     "slow longing blink, whiskers quiver, both ears tip back softly together, his tail sways "
                     "in a low slow arc; his chest lifts in a quiet sigh.", LOCK_L),
    "two_shot":     (CAV / "couple_bar_v3.png",
                     "The camera holds, breathing. A shy beat: the no-tail French bulldog tilts her head then "
                     "gives a bashful shoulder shrug; the orange tabby cat flicks an ear and leans a touch "
                     "closer; both glance away, then back.", LOCK_R + " " + LOCK_L),
    "party_leo":    (CAV / "party_leo_longing_v3.png",
                     "Slow orbit, green and red light pulsing across his face. The orange tabby cat slowly "
                     "turns his head to find her across the crowd, his pupils dilate wide, whiskers flare; a "
                     "yearning stillness, tail-tip twitching.", LOCK_L),
    "party_ryani":  (CAV / "party_ryani_crowd_v3.png",
                     "Slow arc around her, sequins sparking green and red. The no-tail French bulldog tilts "
                     "her head, her ears droop then perk, her mouth opens in a small wistful sigh, then a soft "
                     "shoulder shrug; the crowd sways in soft focus.", LOCK_R),
    "piano_wide":   (CAV / "sc6_performance_v7.png",
                     "Slow dolly through the dark hall toward the piano. The orange tabby cat sways gently "
                     "with the music as he plays, his head dipping, his tail draped over the bench swaying; "
                     "candle flames flicker, dust floats in the amber beam.", LOCK_L),
    "paws":         (CAV / "piano_paws_v2.png",
                     "Macro, shallow focus. The cat's striped paws rise and press the piano keys in slow "
                     "succession; a hammer glints inside the piano; warm bokeh.", LOCK_L),
    "listening":    (CAV / "ryani_listening_v3.png",
                     "Slow push-in. The no-tail French bulldog's ears perk toward the music, she tilts her "
                     "head curiously, her eyes soften, then she gives a slow knowing wink; candle bokeh "
                     "breathes behind.", LOCK_R),
    # ★ KILLPOINTS — most alive, longest at assembly.
    "phone_leo":    (CAV / "phone_leo_cu_v3.png",
                     "Slow push-in, very tight. First the orange tabby cat holds the receiver to one ear, the "
                     "near ear tilting naturally toward it to listen, whiskers still. Then the surprise hits: "
                     "his eyes fly wide and his pupils blow huge, both ears shoot up, his whiskers flare "
                     "forward, his mouth parts in a tiny gasp, and his tail suddenly puffs up thick and "
                     "bristles stiff like a bottlebrush. Then he melts into a trembling overwhelmed blink.", LOCK_L),
    "phone_ryani":  (CAV / "phone_ryani_cu_v3.png",
                     "Slow push-in, warm bokeh. The no-tail French bulldog holds the black receiver against "
                     "her cheek with one front paw. She clearly TILTS her head to one side, her ears perk up, "
                     "her mouth opens as she speaks; then she gives a clear, unmistakable WINK with one eye; "
                     "her white blaze crinkles with feeling.", LOCK_R),
    "ending":       (CAV / "sc7_window_v3.png",
                     "Slow pull-back from the glass. The no-tail French bulldog presses one paw flat on the "
                     "rain-streaked window, watching him go; her ears droop, a slow blink, then a small "
                     "hopeful perk of the ears and a lift of the chin; rain runs down the pane.", LOCK_R),
}


def render(name: str) -> Path:
    still, prompt, lock = CUTS[name]
    still = Path(still)
    if not still.exists():
        raise SystemExit(f"missing still for {name}: {still}")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{name}.mp4"
    full = (prompt + " " + lock).strip()
    print(f"==> {name}: {still.name} -> {out.name}")
    r = subprocess.run([sys.executable, str(SEEDANCE), "--fast", "--mode", "i2v",
                        "--image", str(still), "--prompt", full, "--seconds", "5",
                        "--ratio", "9:16", "--output", str(out)],
                       cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"seedance failed for {name}")
    print(f"    wrote {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Tango C Seedance render driver")
    ap.add_argument("--only", help="comma-separated cut names")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k, (s, _p, _l) in CUTS.items():
            print(f"{k:14s} <- {Path(s).name}")
        return 0
    names = list(CUTS) if a.all else (a.only.split(",") if a.only else [])
    if not names:
        ap.error("pass --only <names>, --all, or --list")
    for n in names:
        render(n.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
