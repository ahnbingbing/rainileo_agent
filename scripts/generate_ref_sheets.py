"""
scripts/generate_ref_sheets.py
------------------------------
Auto-generate the per-character / per-pose reference sheets that the Director
names in `seedance_mode=ref` cuts.

Output slots (match agents/cameraman.py:REF_LIBRARY logical names):
  ryani_solo, leo_solo, ryani_playbow, leo_pounce, leo_question_tail

Each is a 1024x1024 PNG written to assets/character_ref/<name>.png.
Idempotent — skips slots whose file already exists unless --force is given.

Uses OpenAI gpt-image-1 (images.edit) with assets/character_ref/official_ryani_leo.png
as the marking-faithful source reference. Marking strings embedded inline.

Usage:
    # Generate all missing slots
    python3 scripts/generate_ref_sheets.py

    # Regenerate a specific slot, overwriting any existing file
    python3 scripts/generate_ref_sheets.py --slot ryani_playbow --force

    # Generate everything from scratch
    python3 scripts/generate_ref_sheets.py --force

    # Dry-run (no API calls)
    python3 scripts/generate_ref_sheets.py --dry-run

Cost: ~$0.04-0.08 per slot at gpt-image-1 high. 5 slots = ~$0.20-0.40.
"""
from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("generate_ref_sheets")

REF_DIR = ROOT / "assets" / "character_ref"
SOURCE_REF = REF_DIR / "official_ryani_leo.png"

# ── Marking strings (must match agents/prompts/character_sheets.md) ──
RYANI_MARKING = (
    "An old female black French Bulldog (Ryani, age 11). "
    "White markings on her black face: a Boston Terrier white blaze from nose to forehead, "
    "white dot above left eye, white dot above right eye. "
    "Silver-grey aged muzzle, with salt-and-pepper greying also subtly on forehead and cheeks. "
    "White chin. Large prominent white chest patch. "
    "Bat ears (large, upright, rounded tips). No tail. "
    "**Important body proportions (calibrated from real photos):** "
    "Classic compact Frenchie body silhouette BUT with notably **slim bone structure** for the breed — "
    "front legs are visibly slim (clearly thinner than typical show-line Frenchie legs), "
    "ribcage is narrower than a typical Frenchie (NOT barrel-chested). "
    "**HOWEVER, the midsection / waist / belly area should fill out naturally** — this is a healthy "
    "senior female Frenchie, NOT skeletal, NOT pinched at the waist, NOT hourglass-shaped. "
    "The belly has gentle natural fullness; ribs should NOT be visible. "
    "Slim bones, fuller midsection — like a real healthy Ryani. "
    "Still compact and clearly a Frenchie — short legs, solid body, broad short muzzle, bat ears — "
    "just the bones (legs, ribcage) are slimmer than the breed norm, while the waist stays natural. "
    "The large white chest patch can create a 'broader chest' optical illusion — do not overdo chest width. "
    "Subtle wrinkles around shoulders/neck. "
    "**NOT skinny, NOT slender like a Chihuahua, NOT stretched, NOT pinched at waist** — "
    "compact Frenchie with refined leg bones and a naturally full belly. "
    "Only black, white, grey — no brown."
)
LEO_MARKING = (
    "An orange tabby cat (Leo, ~8 months old, young adult). "
    "**Eyes: pale yellow-green / chartreuse** (NOT pure amber, NOT pure green — "
    "closer to yellow-green / lime with subtle gold undertones). Large round expressive eyes, "
    "dark vertical pupils, distinct catchlight sparkle. "
    "**Faint horizontal pink scar across the nose bridge** — small but visible. "
    "Pink nose, white chin tuft, long white whiskers. "
    "**Tonal variation across the body (important — calibrated from real photos):** "
    "saturated warm orange on the back/top with darker amber tabby stripes; "
    "**noticeably paler cream-orange on the cheeks/jaw area** (cheeks read lighter than the forehead); "
    "**cream / pale cream-orange underside on chest and belly** (clearly lighter than the back). "
    "Subtle whisker pads on either side of the nose (mature young-adult feature). "
    "M-shape tabby stripes on forehead, darker amber ring stripes on the tail. "
    "**Face shape: young adult, slightly elongated and lean** — NOT a round chubby baby-kitten face. "
    "**Body: lean and lanky young adult proportions, agile**, NOT a chunky kitten."
)

STYLE_BASE = (
    "Professional pet portrait photograph, 85mm, f/1.8, soft studio lighting, "
    "clean off-white seamless background, centered subject, no other objects, "
    "photorealistic, sharp focus on the subject's face and body. "
    "**STRICT — bare natural fur only.** "
    "The reference image may show the subject(s) wearing Korean hanbok or other clothing — "
    "**you MUST completely remove ALL clothing, hanbok, robes, shirts, jackets, dresses, scarves, "
    "bows, ribbons, hats, costumes, and any fabric covering the body**. "
    "No hanbok. No clothes. No accessories. No bow ties. No collars (except a plain functional collar if specified). "
    "No anthropomorphic poses. The subject(s) appear as completely natural unclothed pets, "
    "with their full fur, markings, and body shape clearly visible across the chest, back, and limbs. "
    "If you see clothing in the reference, IGNORE IT and show only the natural animal underneath. "
)

# ── Per-slot prompts ──
SLOTS = {
    "ryani_solo": (
        f"{STYLE_BASE}"
        f"{RYANI_MARKING} "
        "Full body portrait, sitting upright on a clean background, "
        "facing 3/4 toward camera, calm dignified ladylike expression, ears alert. "
        "Compact Frenchie silhouette with the **slim front legs and narrower ribcage** as described — "
        "you can clearly see her front legs are slimmer than a standard Frenchie's. "
        "Body is still solid and compact, just refined-boned. "
        "Markings clearly visible. Only the dog in frame — no cat, no human, no props."
    ),
    "leo_solo": (
        f"{STYLE_BASE}"
        f"{LEO_MARKING} "
        "Full body portrait, sitting upright on a clean background, "
        "facing 3/4 toward camera, curious wide-eyed expression, "
        "tail raised in a gentle question mark shape, ears forward. "
        "Only the kitten in frame — no dog, no human, no props."
    ),
    "ryani_playbow": (
        f"{STYLE_BASE}"
        f"{RYANI_MARKING} "
        "Full body, performing a play bow: front paws stretched forward flat on the floor, "
        "hind quarters raised in play bow stance, mouth slightly open in playful excitement. "
        "Side / 3-quarter angle showing the bow profile clearly. "
        "Compact Frenchie body in the bow — short solid legs but **slim-boned front legs** as in real life — "
        "with the narrower ribcage of Ryani specifically. "
        "Only the dog in frame — no cat, no human, no props."
    ),
    "leo_pounce": (
        f"{STYLE_BASE}"
        f"{LEO_MARKING} "
        "Crouched low to the ground in hunting stance, body pressed flat, "
        "eyes locked forward, hind quarters wiggling side to side, "
        "weight shifted back as if about to spring forward. "
        "Side / 3-quarter angle showing the crouch profile clearly. "
        "Only the kitten in frame — no dog, no human, no props."
    ),
    "leo_question_tail": (
        f"{STYLE_BASE}"
        f"{LEO_MARKING} "
        "Standing or walking, tail held high and curled into a clear question mark shape — "
        "this is the focal point. Body in profile so the tail silhouette is unambiguous. "
        "Ears perked forward, alert curious expression. "
        "Only the kitten in frame — no dog, no human, no props."
    ),
}


def _build_square_source(src: Path, tmp_png: Path) -> Path:
    """Crop/resize the source ref to a 1024x1024 PNG for OpenAI images.edit."""
    from PIL import Image
    img = Image.open(src).convert("RGB")
    w, h = img.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    img = img.crop((left, top, left + s, top + s))
    img = img.resize((1024, 1024))
    tmp_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(tmp_png, format="PNG")
    return tmp_png


def generate_slot(slot: str, prompt: str, out_path: Path) -> None:
    """Generate one ref sheet via OpenAI gpt-image-1 (images.edit)."""
    from openai import OpenAI
    if not SOURCE_REF.exists():
        raise RuntimeError(
            f"source ref missing: {SOURCE_REF}. "
            "Need this as the marking-faithful seed for images.edit."
        )
    client = OpenAI()
    tmp_png = ROOT / "data" / "tmp" / "_ref_seed.png"
    _build_square_source(SOURCE_REF, tmp_png)

    log.info("Generating %s -> %s (prompt %d chars)", slot, out_path.name, len(prompt))
    result = client.images.edit(
        model="gpt-image-1",
        image=open(tmp_png, "rb"),
        prompt=prompt,
        size="1024x1024",
        quality="high",
        n=1,
    )
    png_bytes = base64.b64decode(result.data[0].b64_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)
    log.info("  wrote %s (%d KB)", out_path, len(png_bytes) // 1024)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(description="Generate Seedance ref sheets")
    p.add_argument("--slot", action="append", default=[],
                   help="generate only this slot (repeatable). Default: all missing.")
    p.add_argument("--force", action="store_true",
                   help="regenerate even if output file already exists")
    p.add_argument("--dry-run", action="store_true",
                   help="print plan without calling OpenAI")
    args = p.parse_args()

    selected = args.slot or list(SLOTS.keys())
    unknown = [s for s in selected if s not in SLOTS]
    if unknown:
        print(f"ERROR: unknown slot(s): {unknown}. Known: {list(SLOTS)}", file=sys.stderr)
        return 2

    plan: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for slot in selected:
        out = REF_DIR / f"{slot}.png"
        if out.exists() and not args.force:
            skipped.append(slot)
            continue
        plan.append((slot, out))

    print(f"Plan: generate {len(plan)} slots, skip {len(skipped)} existing")
    for slot, out in plan:
        print(f"  → {slot} -> {out.relative_to(ROOT)}")
    if skipped:
        print(f"  skip: {', '.join(skipped)} (use --force to overwrite)")

    if args.dry_run:
        print("[dry-run] no API calls")
        return 0

    if not plan:
        print("Nothing to do.")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    for slot, out in plan:
        try:
            generate_slot(slot, SLOTS[slot], out)
        except Exception as e:
            log.exception("Slot %s failed", slot)
            print(f"ERROR: slot {slot} failed: {e}", file=sys.stderr)
            return 3

    print(f"\nDone. {len(plan)} ref sheets generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
