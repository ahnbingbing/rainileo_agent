#!/usr/bin/env python3
"""Stage 5 — MV still board (gpt-image-2, ref-lock) + contact sheet.

Reuses the PROVEN gpt-image-2 image-edit path (scripts/mv_brillion_gpt.py): character
identity rides on editing from the REAL character photos (leo_real / ryani_real), the
scene is described in text. Per PD 2026-11-18, gpt-image-2 is far more photoreal than
nano-banana and runs locally. Character/anatomy/no-tail/no-human guards are injected so
the documented drift classes (Ryani tail/neck-white/male-anatomy, human intrusion) can't
ship (§8.1, §8.14, §6).

Version-managed: never overwrites (§7) — writes {tag}_v{NN}.png, monotonic.
"""
from __future__ import annotations

import argparse
import base64
import logging
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "assets" / "character_ref"
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

log = logging.getLogger("scripts.mv_still")

# Proven concise identity blocks (scripts/mv_brillion_gpt.py). Ref-lock (editing the real
# photo) carries the fine detail; this text reinforces the load-bearing markings.
LEO = ("a real photorealistic orange tabby cat (warm orange tabby, dark M-shape forehead stripes, "
       "pale cream cheeks and chest, large pale chartreuse yellow-green eyes, pink nose, white whiskers, cat tail)")
RYA = ("a real photorealistic small black FEMALE French bulldog with a NORMAL, compact, healthy build — a "
       "fine-boned-for-a-Frenchie frame (slim legs, a slightly narrower-than-typical chest) BUT a natural "
       "medium body with gentle belly fullness: NOT skinny/thin/whippet-lean AND NOT an overweight broad "
       "stocky show-Frenchie. Her FACE is a normal flat-faced French bulldog face, gently refined and "
       "feminine with a short flat brachycephalic muzzle — do NOT broaden it into wide jowly puffed cheeks "
       "and do NOT thin it into a narrow gaunt face; keep natural Frenchie proportions. Big upright SMOOTH "
       "bat ears with short sleek fur and NO fur tufts/feathering/fluff on or inside the ears (a Frenchie's "
       "ears are smooth). NO TAIL at all. Her white markings stay FINE and clear — a THIN pencil-width white "
       "blaze up the muzzle to the forehead (never a thick or wide stripe/splash), a neat white chin and a "
       "big white chest patch, white toe tips; nape and whole back SOLID BLACK with no white spot)")
# Ryani anatomy guard (§6): she is spayed female — gpt-image must not add male anatomy on splay/belly poses.
RYA_ANAT = ("Ryani's underbelly between her hind legs is completely smooth, flat, featureless black fur — "
            "no genitalia of any kind.")
NO_HUMANS = "No humans anywhere in the frame — no hands, no arms, no people; only the pet(s)."
PHOTO = ("Photorealistic cinematic DSLR photograph, real fur and real materials, natural photographic "
         "lighting, NOT cartoon, NOT CGI, NOT illustration. 9:16 vertical.")

LEOP = str(REF / "leo_real.png")
RYAP = str(REF / "ryani_real.png")


def _refs_and_identity(subject: str) -> tuple[list[str], str]:
    subject = (subject or "both").lower()
    if subject == "ryani":
        return [RYAP], f"Ryani is {RYA}. {RYA_ANAT}"
    if subject == "leo":
        return [LEOP], f"Leo is {LEO}."
    # Size: Ryani (French bulldog) is SLIGHTLY BIGGER than Leo (cat) — PD 2026-11-19 refinement.
    return [LEOP, RYAP], f"Leo is {LEO}. Ryani is {RYA}. {RYA_ANAT} Ryani the French bulldog is SLIGHTLY BIGGER / a bit larger than Leo the cat."


# Verbatim constraint pasted into EVERY still when a canonical reference is set — the recurring
# car/setting + the pets' relative size must be IDENTICAL to the canonical reference, because text
# alone can't lock an object's geometry (the cross-shot drift root: car body / wheel size / pet size).
# "Boring consistency in the prompt → consistency in the output" (Seedance drama workflow).
CANONICAL_LOCK = (
    "★CANONICAL CONSISTENCY: the car and its setting are ONE fixed thing, IDENTICAL in every shot — "
    "match the canonical reference image exactly for the car body, the chrome wire wheels, the "
    "normal-size LEFT-side steering wheel, and the pets' relative size (the dog is slightly bigger "
    "than the cat). Do NOT redesign the car, resize the wheel, or change the pets' build between shots.")


def build_prompt(cut: dict, style: str | None = None, canonical: bool = False) -> str:
    """Assemble the gpt-image-2 prompt: identity + scene + unifying art-direction + guards + realism.
    `style` (manifest art_direction) sets the SHARED look (e.g. mid-century checkerboard carpet) and
    comes AFTER the per-cut scene so it governs the floor/background palette across every cut.
    `canonical` appends the verbatim consistency lock (used when a canonical reference image is fed)."""
    _, identity = _refs_and_identity(cut.get("subject", "both"))
    scene = (cut.get("still_prompt") or cut.get("action") or "").strip()
    parts = [identity, scene, (style or "").strip(),
             CANONICAL_LOCK if canonical else "", NO_HUMANS, PHOTO]
    return " ".join(p for p in parts if p)


def _next_version(out_dir: Path, tag: str) -> int:
    existing = sorted(out_dir.glob(f"{tag}_v*.png"))
    if not existing:
        return 1
    nums = [int(p.stem.rsplit("_v", 1)[1]) for p in existing if p.stem.rsplit("_v", 1)[-1].isdigit()]
    return (max(nums) + 1) if nums else 1


def gen_still(cut: dict, out_dir: Path, *, size: str = "1024x1536", style: str | None = None,
              canonical_ref: str | None = None) -> Path:
    """Generate one still for a cut via gpt-image-2 ref-lock. Version-managed (no overwrite).
    `canonical_ref` = a per-MV canonical asset reference image (the recurring car + pets at correct
    relative size). Added to the ref-lock so the CAR/wheel/pet-size are anchored by IMAGE, not just
    text — the fix for cross-shot drift (research: canonical reference per recurring entity)."""
    from openai import OpenAI
    client = OpenAI()
    refs, _ = _refs_and_identity(cut.get("subject", "both"))
    use_canon = bool(canonical_ref) and Path(canonical_ref).exists()
    if use_canon:
        refs = list(refs) + [canonical_ref]
    prompt = build_prompt(cut, style=style, canonical=use_canon)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = cut["tag"]
    ver = _next_version(out_dir, tag)
    out = out_dir / f"{tag}_v{ver:02d}.png"
    fhs = [open(p, "rb") for p in refs]
    try:
        r = client.images.edit(model="gpt-image-2", image=fhs, prompt=prompt, size=size)
        out.write_bytes(base64.b64decode(r.data[0].b64_json))
        log.info("still OK %s (v%02d)", tag, ver)
    finally:
        for f in fhs:
            f.close()
    return out


def build_contact_sheet(pngs: list[Path], out: Path, *, cols: int = 3, cell: int = 420) -> Path:
    """Full-res-derived montage for PD QC (§6: identity/markings judged on full-res, tiles for layout)."""
    from PIL import Image, ImageDraw
    pngs = [p for p in pngs if Path(p).exists()]
    if not pngs:
        raise ValueError("no stills to montage")
    rows = (len(pngs) + cols - 1) // cols
    cw, ch = cell, int(cell * 16 / 9)
    sheet = Image.new("RGB", (cols * cw, rows * ch), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(pngs):
        im = Image.open(p).convert("RGB")
        im.thumbnail((cw, ch))
        x, y = (i % cols) * cw, (i // cols) * ch
        sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
        d.text((x + 6, y + 6), Path(p).stem, fill=(255, 235, 120))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def rf_thumb(cut: dict, out_dir: Path) -> Path | None:
    """Extract a representative frame from an RF cut's matched clip (at the trim window's
    midpoint) so the PD contact sheet shows the REAL footage backbone next to the AV stills."""
    import subprocess
    clip = cut.get("rf_clip")
    if not clip:
        return None
    src = Path(clip)
    if not src.is_absolute():
        src = ROOT / clip
    if not src.exists():
        return None
    ss = float(cut.get("ss") or 0.0) + float(cut.get("duration") or 2.0) / 2.0
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{cut['tag']}_rf.png"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", str(src), "-frames:v", "1",
         "-vf", "scale=1024:-2", str(out)],
        capture_output=True, text=True)
    return out if (r.returncode == 0 and out.exists()) else None


def build_board_sheet(m: dict, out_dir: Path, sheet_out: Path) -> tuple[Path, list[dict]]:
    """Hybrid board for the still_qc gate: RF cuts → a frame from the real clip, AV cuts →
    the latest gpt-image-2 still. One montage in cut order + a per-cut summary for PD."""
    tiles: list[Path] = []
    summary: list[dict] = []
    for c in m.get("cuts", []):
        if c.get("still_strategy") == "real_footage":
            t = rf_thumb(c, out_dir)
            summary.append({"tag": c["tag"], "type": "RF", "subject": c.get("subject"),
                            "clip": c.get("rf_asset_id"), "ss": c.get("ss"),
                            "dur": c.get("duration"), "score": c.get("rf_score"),
                            "lyric": c.get("lyric")})
        else:
            vs = sorted(out_dir.glob(f"{c['tag']}_v*.png"))
            t = vs[-1] if vs else None
            summary.append({"tag": c["tag"], "type": "AV", "subject": c.get("subject"),
                            "still": t.name if t else None, "dur": c.get("duration"),
                            "lyric": c.get("lyric")})
        if t:
            tiles.append(t)
    sheet = build_contact_sheet(tiles, sheet_out) if tiles else None
    return sheet, summary


def main() -> int:
    from agents.mv_producer import load_manifest, mv_dir
    ap = argparse.ArgumentParser(description="MV still board — gpt-image-2 ref-lock")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--only", help="comma tags to render (e.g. c02_ryani_stretch,c12_killpoint_dualfreeze)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--contact", action="store_true", help="just (re)build the contact sheet from latest versions")
    a = ap.parse_args()
    m = load_manifest(a.slug)
    cuts = m.get("cuts", [])
    out_dir = mv_dir(a.slug) / "stills"

    if a.contact:
        latest = []
        for c in cuts:
            vs = sorted(out_dir.glob(f"{c['tag']}_v*.png"))
            if vs:
                latest.append(vs[-1])
        sheet = build_contact_sheet(latest, mv_dir(a.slug) / "contact_sheet.png")
        print("contact sheet →", sheet)
        return 0

    style = m.get("art_direction")  # shared look (mid-century checkerboard carpet etc.)
    # per-MV canonical asset reference (recurring car + pets at correct size) — image anchor against
    # cross-shot drift. Kill-switch MV_CANONICAL_REF=0. Resolve relative to repo root.
    canonical_ref = m.get("canonical_ref")
    if canonical_ref and not os.path.isabs(canonical_ref):
        canonical_ref = str(ROOT / canonical_ref)
    if os.getenv("MV_CANONICAL_REF", "1") == "0":
        canonical_ref = None
    want = set((a.only or "").split(",")) if a.only else None
    # never generate stills for RF cuts — those ride real footage, not gpt-image
    todo = [c for c in cuts if (a.all or (want and c["tag"] in want))
            and c.get("still_strategy") != "real_footage"]
    if not todo:
        ap.error("--only <tags> or --all or --contact (no AV cuts matched)")
    done = []
    for c in todo:
        try:
            p = gen_still(c, out_dir, style=style, canonical_ref=canonical_ref)
            print("OK", p.name)
            done.append(p)
        except Exception as e:  # noqa: BLE001
            print("FAIL", c["tag"], str(e)[:200])
    if done:
        build_contact_sheet(done, mv_dir(a.slug) / "contact_sheet.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
