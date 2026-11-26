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

# Identity blocks. The ref-lock (editing from leo_real/ryani_real) carries the fine detail, so this
# text only names the LOAD-BEARING markings and the documented drift classes the image alone fails to
# prevent (§8.1, §8.14). Per the official gpt-image guide the per-pass preservation reminder is kept
# SHORT and specific — a long identity paragraph drowns the cut's own action (the c10 lesson). Keep
# every real guard; just say it tight.
LEO = ("a photorealistic orange tabby cat — dark M-stripe forehead, cream cheeks/chest, large pale "
       "chartreuse yellow-green eyes, pink nose, one orange cat tail")
RYA = ("a photorealistic small black FEMALE French bulldog, NO TAIL — compact healthy build (not "
       "whippet-thin, not stocky-broad), flat brachycephalic face, smooth upright bat ears with no fur "
       "tufts. Markings stay fine: a THIN pencil-width white blaze up the muzzle (never thick/wide), a "
       "neat white chin and chest patch, white toe tips; nape and whole back solid black")
# Ryani anatomy guard (§6): she is spayed female — gpt-image must not add male anatomy on splay/belly poses.
RYA_ANAT = "Ryani's underbelly is smooth featureless black fur, no genitalia."
# Cat-tail continuity guard: gpt-image renders Leo's tail as a SEPARATE floating object or drapes it on
# the car. His tail is ONE continuous part of his body.
LEO_TAIL = ("Leo is one whole cat: his orange tail grows from his own rump and stays attached — no "
            "separate, floating, or detached orange tail anywhere, never draped on the car.")
NO_HUMANS = "No humans anywhere in the frame — no hands, no arms, no people; only the pet(s)."
# Physical-plausibility prior (VLIPP-style). T2I models follow text but violate physical commonsense
# (PhyBench/OmniPhys) — wrong steering side, a pet outside/clipping the car, warped scale, ghosted
# limbs. Stated as HARD facts; a physically-clean still is also a cleaner i2v keyframe (PhysGen/K2V).
PHYSICS = ("Physically correct: the steering wheel is on the LEFT. Each pet's whole body is inside the "
           "cabin — nothing outside, on top of, or clipping the car. Correct scale — the dog only "
           "slightly bigger than the cat. Exactly one clean body per animal, no doubled or ghosted "
           "limbs; obey gravity, nothing floats or merges into the car.")
PHOTO = ("Photorealistic cinematic DSLR photograph, real fur and real materials, natural photographic "
         "lighting, NOT cartoon, NOT CGI, NOT illustration. 9:16 vertical.")

LEOP = str(REF / "leo_real.png")
RYAP = str(REF / "ryani_real.png")


def _refs_and_identity(subject: str) -> tuple[list[str], str]:
    subject = (subject or "both").lower()
    if subject == "ryani":
        return [RYAP], f"Ryani is {RYA}. {RYA_ANAT}"
    if subject == "leo":
        return [LEOP], f"Leo is {LEO}. {LEO_TAIL}"
    # Size: Ryani (French bulldog) is SLIGHTLY BIGGER than Leo (cat) — PD 2026-11-19 refinement.
    return [LEOP, RYAP], (f"Leo is {LEO}. {LEO_TAIL} Ryani is {RYA}. {RYA_ANAT} Ryani the French "
                          "bulldog is SLIGHTLY BIGGER / a bit larger than Leo the cat.")


# Canonical role-label (official gpt-image multi-ref guide: give each input ref a ROLE, don't re-describe
# in text what the image already shows). The LAST ref image is the canonical car+pets; text alone can't
# lock geometry (the cross-shot drift root), so the image carries it and this only names the role +
# the few invariants the image can't disambiguate (LEFT wheel, relative scale, windshield-clip physics).
CANONICAL_LOCK = (
    "CANONICAL REF (final image): match the car exactly — same body, chrome wire wheels, and normal-size "
    "LEFT-side steering wheel; keep the pets small and seated at the same relative size (dog slightly "
    "bigger than cat), not giant heads filling the car. The windshield frame is a solid object in front "
    "of the cabin — ears stay clear of it, never clipping through.")


# Shot-size must be REALIZED in the still, not just labeled — else every cut comes out a medium
# two-shot (the canonical ref biases medium) and the i2v push-in makes them all feel the same (the
# zoom sawtooth / "same composition" PD flagged). Reference grammar (Blinding Lights, real 151-shot
# analysis) is WS/MS-dominant with CU/ECU inserts — so FORCE the framing. This leads the prompt so it
# overrides the medium bias. Kill-switch MV_SHOTSIZE=0.
# Framing describes the FRAMING ONLY — never names example objects (that was the c10 bug: the insert
# template listed "dashboard gauge" and the model drew it, overriding the actual DETAIL). The cut's
# own DETAIL/ACTION line names the subject; framing just sets how tight and from where.
_FRAMING = {
    "ECU":    "Extreme close-up: fill the frame with the single detail named below; everything else out of frame.",
    "CU":     "Close-up: the named subject fills most of the frame, tight and intimate, shallow depth of field.",
    "MS":     "Medium shot: the two pets from the chest up in the car, balanced, some surroundings visible.",
    "WS":     "Wide shot: the whole car is SMALL in a big neon cityscape with lots of environment; the pets "
              "are tiny — read as silhouette and base colour (dark dog, orange cat), markings not enlarged.",
    "insert": "Tight insert / detail shot: fill the frame with ONLY the object or detail named below — "
              "the pets are NOT the subject of this shot.",
    "aerial": "Aerial / high crane: looking down on the car from high above, the city spread below.",
}


def is_rear_view(cut: dict) -> bool:
    """A cut whose composition looks at the car FROM BEHIND. The canonical reference image is a
    front/side view, so as an IMAGE anchor it beats the text 'from behind' (image anchor > text — the
    c22 front-view defect). For these cuts gen_still drops the canonical image so the text viewpoint
    wins, and build_prompt adds a terse rear-camera directive."""
    d = (cut.get("still_prompt") or cut.get("action") or "").lower()
    return ("from behind" in d or "rear view" in d or "behind the" in d
            or ("taillight" in d and "rear" in d))


_REAR_CAMERA = ("CAMERA IS BEHIND THE CAR looking forward from the rear: we see the back of the car and "
                "the pets from behind; the empty back seat fills the near foreground, the pets sit ahead "
                "in the front seats and turn their heads back over their shoulders toward this rear "
                "camera. Clear depth: foreground seat, mid-ground pets, distant sky beyond.")


def build_prompt(cut: dict, style: str | None = None, canonical: bool = False,
                 scene_setting: str | None = None, rear: bool = False) -> str:
    """Assemble the gpt-image EDIT prompt following the OFFICIAL guide (notes/model_prompting_guides.md
    §gpt-image): edit-framing + hierarchical labeled order SCENE → SHOT → DETAIL/ACTION → KEEP →
    EXCLUDE → medium, short segments, and NO self-contradiction.

    ★Context-sensitive (the fix for the c10 'dashboard on a blanket insert' disaster): an INSERT /
    detail shot has the pets as NOT the subject, so it drops the pet-identity, canonical and physics
    walls that otherwise drown the actual detail and force 'pets in the car'. It gets only the detail +
    world palette. Pet-subject cuts keep a KEEP-list (identity/world/physics) AFTER the action, so the
    creative content leads and the constraints follow (guide order), not the other way round."""
    shot = (cut.get("shot_size") or "").strip()
    is_insert = shot == "insert"
    detail = (cut.get("still_prompt") or cut.get("action") or "").strip()
    framing = _FRAMING.get(shot, "") if os.getenv("MV_SHOTSIZE", "1") != "0" else ""
    scene = ""
    if scene_setting and os.getenv("MV_SCENE_SETTING", "1") != "0":
        scene = f"SCENE: {scene_setting.strip()}."
    world = (style or "").strip()
    head = "Edit the reference photos into one new cinematic still."
    exclude = "EXCLUDE: no humans, no hands, no people, no text, no watermark."

    if is_insert:
        parts = [head, scene, (f"SHOT — {framing}" if framing else ""),
                 (f"DETAIL: {detail}." if detail else ""),
                 (f"STYLE: {world}" if world else ""), exclude, PHOTO]
        return " ".join(p for p in parts if p)

    _, identity = _refs_and_identity(cut.get("subject", "both"))
    parts = [head, scene, (f"SHOT — {framing}" if framing else ""),
             (_REAR_CAMERA if rear else ""),
             (f"ACTION: {detail}." if detail else ""),
             f"KEEP SUBJECTS (preserve from the reference photos): {identity}",
             (f"KEEP WORLD: {world}" if world else ""),
             (CANONICAL_LOCK if canonical else ""), PHYSICS, exclude, PHOTO]
    return " ".join(p for p in parts if p)


def _next_version(out_dir: Path, tag: str) -> int:
    existing = sorted(out_dir.glob(f"{tag}_v*.png"))
    if not existing:
        return 1
    nums = [int(p.stem.rsplit("_v", 1)[1]) for p in existing if p.stem.rsplit("_v", 1)[-1].isdigit()]
    return (max(nums) + 1) if nums else 1


def gen_still(cut: dict, out_dir: Path, *, size: str = "1024x1536", style: str | None = None,
              canonical_ref: str | None = None, scene_setting: str | None = None) -> Path:
    """Generate one still for a cut via gpt-image-2 ref-lock. Version-managed (no overwrite).
    `canonical_ref` = a per-MV canonical asset reference image (the recurring car + pets at correct
    relative size). Added to the ref-lock so the CAR/wheel/pet-size are anchored by IMAGE, not just
    text — the fix for cross-shot drift (research: canonical reference per recurring entity).
    `scene_setting` = this cut's scene location/time-of-day (kept coherent across the scene)."""
    from openai import OpenAI
    client = OpenAI()
    refs, _ = _refs_and_identity(cut.get("subject", "both"))
    rear = is_rear_view(cut)
    # Drop the canonical IMAGE when it would fight the intended framing. The canonical is a MEDIUM
    # two-shot, so as an image anchor it (a) overrides a rear viewpoint with its front/side pose and
    # (b) pulls an intimate face CU back to a medium two-shot (the c16 hook defect). In both cases the
    # car is barely in frame, so losing its image-anchor costs little while the framing finally lands.
    # `drop_canonical` is the explicit per-cut opt-out for tight-intimacy shots the Director marks.
    drop_canon = rear or bool(cut.get("drop_canonical"))
    use_canon = bool(canonical_ref) and Path(canonical_ref).exists() and not drop_canon
    if use_canon:
        refs = list(refs) + [canonical_ref]
    prompt = build_prompt(cut, style=style, canonical=use_canon, scene_setting=scene_setting, rear=rear)
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
    # Scene settings (location/time per scene) — each cut's scene_id resolves to its setting so a
    # scene stays coherent across varied angles (handoff #3 scene coherence). Kill-switch MV_SCENE_SETTING=0.
    scene_by_id = {s.get("scene_id"): s.get("setting") for s in (m.get("scenes") or []) if s.get("scene_id")}
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
            p = gen_still(c, out_dir, style=style, canonical_ref=canonical_ref,
                          scene_setting=scene_by_id.get(c.get("scene_id")))
            print("OK", p.name)
            done.append(p)
        except Exception as e:  # noqa: BLE001
            print("FAIL", c["tag"], str(e)[:200])
    if done:
        build_contact_sheet(done, mv_dir(a.slug) / "contact_sheet.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
