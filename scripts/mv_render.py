"""Stage 6 — AV Seedance FAST i2v render (manifest-driven, generic).

Reuses the PROVEN brillion render path (scripts/mv_brillion_seedance.py): each AV cut's
approved gpt-image-2 still → Seedance FAST i2v (5s, 9:16) with a MOTION-ONLY prompt.
Unlike the brillion hardcoded dict this reads the manifest, so it works for any MV.

Motion-prompt contract (Seedance best-practices, notes/model_prompting_guides.md §Seedance +
memory mv_generator_prompt_footguns):
  • ONE primary + ONE secondary motion, "slow/gently"; the director's motion_prompt is the seed.
  • ONE camera move stated in pacing words (from the cut's camera_move).
  • Character LOCK appended, POSITIVE-phrased (Seedance is negation-averse → "tailless, short
    smooth rear" beats "no tail"; cat tail sways). This fights the Ryani-tail / Leo-drift classes.
  • Footgun sanitizer: anthropomorphic "arms/legs" → "front/back legs"; strip cheap
    "confetti/sparkles rain down" clauses (PD 2026-11-19 killed the cheap-glitter look).

RF cuts are skipped (they ride real footage — make_mv trims them at assemble).
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDANCE = ROOT / "scripts" / "animate_seedance_i2v.py"
log = logging.getLogger("scripts.mv_render")

# Positive-phrased character locks (Seedance negation-averse). Keep load-bearing markings.
LOCK_RYANI = ("Keep the small black French bulldog exactly — flat bat-eared face, thin white blaze, "
              "white chin and chest, white toe tips, slim build; she stays TAILLESS with a short smooth "
              "rounded rear.")
# Cat-tail continuity (i2v): Seedance can detach Leo's orange tail into a separate floating shape
# behind the car as it animates. His tail stays physically attached to his own body — one whole cat,
# no stray floating orange tail/shape.
_LEO_TAIL_I2V = ("his long orange tail stays attached to his own body and sways gently, never a "
                 "separate or floating orange shape")
LOCK_LEO = ("Keep the orange tabby cat exactly — dark M-forehead stripes, pale chest, chartreuse eyes, "
            f"and {_LEO_TAIL_I2V}.")
LOCK_BOTH = ("Keep the orange tabby cat (" + _LEO_TAIL_I2V + ") and the small black French bulldog "
             "(bat ears, white blaze and chest, slim, TAILLESS with a short smooth rear) exactly — the "
             "dog never has a tail, and both stay comparable in size.")

# Camera-move → Seedance phrasing. The conti-Director emits an OPEN vocabulary (lateral tracking,
# static hold, crane-down orbit, low side tracking, whip pan …), so resolve by KEYWORD rather than
# exact match — else a new move silently falls back to generic "gently" and the drive's lateral
# tracking (the move that carries the cruise continuity) never reaches i2v. Order = most motion-rich
# first so a compound like "crane-down orbit" keeps its motion and "static"/"hold" only win when
# nothing else matches.
_CAM_RULES = [
    (("lateral track", "side track", "tracking"), "Camera tracks smoothly alongside them, moving laterally with the motion."),
    (("orbit",), "Camera slowly orbits around them."),
    (("push-in", "push in", "zoom-in", "zoom in"), "Camera slowly pushes in."),
    (("zoom-out", "zoom out", "pull back", "pullback"), "Camera slowly pulls back."),
    (("crane up", "crane-up"), "Camera slowly cranes up."),
    (("crane down", "crane-down"), "Camera slowly cranes down."),
    (("crane", "aerial"), "Camera slowly cranes over them."),
    (("tilt-up", "tilt up"), "Camera slowly tilts up."),
    (("tilt-down", "tilt down", "tilt"), "Camera slowly tilts down."),
    (("whip", "pan"), "Camera whips quickly across to them."),
    (("static", "hold", "lock"), "Camera holds still."),
]


def _cam_phrase(cam: str | None) -> str:
    c = (cam or "").lower().strip()
    for keys, phrase in _CAM_RULES:
        if any(k in c for k in keys):
            return phrase
    return "Camera moves slowly and gently."


def _lock(subject: str) -> str:
    s = (subject or "both").lower()
    return LOCK_RYANI if s == "ryani" else LOCK_LEO if s == "leo" else LOCK_BOTH


def _sanitize(motion: str) -> str:
    """Strip the known Seedance footguns from the director's motion seed."""
    m = motion or ""
    # anthropomorphic limb words → four-legged animal anatomy
    m = re.sub(r"\barms and legs\b", "front and back legs", m, flags=re.I)
    m = re.sub(r"\barms\b", "front paws", m, flags=re.I)
    # cheap glitter/confetti (PD 2026-11-19): remove any "...sparkles/confetti ... rain/fall down" clause
    m = re.sub(r"[,;]?\s*(digital\s+)?(sparkles?|confetti|glitter)[^.;,]*\b(rain|fall|shower|pour)[^.;,]*",
               "", m, flags=re.I)
    m = re.sub(r"\s{2,}", " ", m).strip(" ,;")
    return m


def motion_prompt(cut: dict) -> str:
    """Compose the Seedance motion prompt: sanitized motion + camera pacing + positive char lock."""
    motion = _sanitize(cut.get("motion_prompt") or cut.get("action") or "gentle idle motion")
    cam = _cam_phrase(cut.get("camera_move"))
    # Anti zoom-sawtooth (PD: "또 만들면 줌인 줌아웃 또 생길 것 같은데"). Seedance's default drift is to
    # push in on EVERY cut; independently generated, those internal zooms concatenate into the zoom
    # sawtooth at every cut boundary — the root PD flags, independent of still shot-size variety. So
    # unless THIS cut intentionally zooms (push-in / pull-back emphasis), pin a constant focal length.
    if "pushes in" not in cam and "pulls back" not in cam:
        cam += " Hold a constant focal length — do not zoom in or out; only the stated camera move happens."
    if not motion.endswith((".", "!")):
        motion += "."
    return f"{motion} {cam} {_lock(cut.get('subject'))}"


def render_cut(cut: dict, out_dir: Path, *, seconds: int = 5, fast: bool = True) -> Path | None:
    still = cut.get("still", {}).get("path")
    if not still:
        raise SystemExit(f"cut {cut['tag']} has no still to animate")
    still_p = still if Path(still).is_absolute() else str(ROOT / still)
    if not Path(still_p).exists():
        raise SystemExit(f"still missing on disk for {cut['tag']}: {still_p}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{cut['tag']}.mp4"
    prompt = motion_prompt(cut)
    cmd = [sys.executable, str(SEEDANCE), "--mode", "i2v", "--image", still_p,
           "--prompt", prompt, "--seconds", str(seconds), "--ratio", "9:16", "--output", str(out)]
    if fast:
        cmd.append("--fast")
    print(f"==> {cut['tag']}: {Path(still_p).name} -> {out.name}")
    print(f"    {prompt[:120]}")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0 or not out.exists():
        print(f"    FAILED {cut['tag']}")
        return None
    return out


def render_manifest(m: dict, *, only: set | None = None, progress_cb=None, fast: bool = True) -> dict:
    from agents.mv_producer import ROOT as PROOT, mv_dir, cut_needs_render, save_manifest
    out_dir = mv_dir(m["slug"]) / "cuts"
    for c in m["cuts"]:
        if c.get("still_strategy") == "real_footage":
            continue  # RF cut — trimmed from real footage at assemble, no Seedance
        if only and c["tag"] not in only:
            continue
        if not only and not cut_needs_render(c):
            continue  # up-to-date render exists
        if progress_cb:
            progress_cb(f":clapper: [render] Seedance i2v {c['tag']} ({c.get('subject')})")
        # Resilient per-cut: Seedance FAST fails transiently (API/network). Retry a few times and,
        # on persistent failure, SKIP this cut rather than crash the whole batch — a 23-cut MV render
        # must not die on one bad call. stage_render's post-check reports any cut left unrendered.
        out = None
        for attempt in range(3):
            try:
                out = render_cut(c, out_dir, fast=fast)
                if out and Path(out).exists():
                    break
                out = None
            except Exception as e:  # noqa: BLE001
                log.warning("render_cut %s attempt %d failed: %s", c["tag"], attempt + 1, str(e)[:120])
                out = None
                time.sleep(8)
        if out:
            try:
                rel = str(out.relative_to(PROOT))
            except ValueError:
                rel = str(out)
            c["render"]["mp4"] = rel
            c["render"]["still_hash_at_render"] = c["still"].get("hash")
            c["change_flags"]["render_dirty"] = False
            # Persist progress after EACH cut: a long i2v batch can die mid-way (network blip), and
            # without an incremental save the done cuts' render paths are lost → the resume re-renders
            # everything. Saving here makes cut_needs_render skip the finished cuts on re-run.
            try:
                save_manifest(m)
            except Exception:  # noqa: BLE001 — never let a save hiccup abort the render
                pass
        elif progress_cb:
            progress_cb(f":warning: [render] {c['tag']} failed after retries — skipped (re-run to fill)")
    return m


def main() -> int:
    from agents.mv_producer import load_manifest, save_manifest
    ap = argparse.ArgumentParser(description="MV AV Seedance render (manifest-driven)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--only", help="comma cut tags")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true", help="print composed motion prompts, render nothing")
    ap.add_argument("--standard", action="store_true", help="use standard (slower) model instead of FAST")
    a = ap.parse_args()
    m = load_manifest(a.slug)
    if a.list:
        for c in m["cuts"]:
            if c.get("still_strategy") == "real_footage":
                print(f"[{c['tag']}] RF — real footage, no Seedance")
                continue
            print(f"[{c['tag']}] {c.get('subject')}\n   {motion_prompt(c)}")
        return 0
    only = set(a.only.split(",")) if a.only else None
    if not (only or a.all):
        ap.error("--only <tags> / --all / --list")
    render_manifest(m, only=only, progress_cb=print, fast=not a.standard)
    save_manifest(m)
    print("saved", a.slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
