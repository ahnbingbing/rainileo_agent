"""scripts/mv_brillion_gpt.py — gpt-image-2 (OpenAI) render of the 브릴리언 몸값 레오 board.
PD 2026-11-18: nano-banana read too 'animation-ish'; gpt-image-2 is far more photoreal
(and IS available locally). Re-render all 15 cuts photoreal via gpt-image-2 image-edit
from the real character photos, so PD can compare the full board vs the nano-banana one.

Char identity = edit from the REAL photos (leo_real / ryani_real). Scene described in text.
Usage: python scripts/mv_brillion_gpt.py --only 01,04 | --all
"""
from __future__ import annotations
import argparse, base64, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "assets/character_ref"
OUT = ROOT / "data/tmp/mv/brillion/board"
try:
    from dotenv import load_dotenv; load_dotenv(ROOT/".env")
except Exception:
    pass
from openai import OpenAI
CLIENT = OpenAI()

LEO = ("a real photorealistic orange tabby cat (warm orange tabby, dark M-shape forehead stripes, pale "
       "cream cheeks and chest, large pale chartreuse yellow-green eyes, pink nose, white whiskers, cat tail)")
RYA = ("a real photorealistic small black FEMALE French bulldog (flat brachycephalic face, big upright bat "
       "ears, NO TAIL, SLIM refined build, a THIN pencil-width white blaze, white chin and chest, white toe "
       "tips; nape and back solid black)")
WEAR = "Y2K idol streetwear — baggy jersey, gold chain, backwards snapback cap, tan Timberland boots on all four paws"
PHOTO = ("Photorealistic cinematic DSLR photograph, real fur and real materials, natural photographic "
         "lighting, NOT cartoon, NOT CGI, NOT illustration. 9:16 vertical.")
LEOP = str(REF/"leo_real.png"); RYAP = str(REF/"ryani_real.png")

# cut -> (list of input image paths, prompt)
B = {
 "01": ([LEOP], f"A tight shot of {LEO} lounging on a lit window ledge in a cool deep-BLUE neon room with "
        f"vertical light strips, just waking with a cool unbothered half-lidded look, cyan rim light. He "
        f"wears a gold chain and a backwards snapback (bare paws, no boots). {PHOTO}"),
 "02": ([LEOP, RYAP], f"{LEO} and beside him {RYA} both walking LOW ON ALL FOUR LEGS (real animals, not "
        f"upright) toward camera out of a glowing blue circular tunnel/portal. Both in {WEAR}. Keep Ryani "
        f"slim with a THIN blaze. {PHOTO}"),
 "03": ([LEOP], f"{LEO} sitting in a confident glam pose in front of a bold POP-ART comic backdrop (red/"
        f"blue/yellow halftone comic panels with a 'MEOW!' speech bubble). He wears a gold chain and "
        f"backwards snapback. Vivid pop colors, medium shot. {PHOTO}"),
 "04": ([LEOP], f"{LEO} walking as a real cat straight toward camera down a dark stage raked by bright "
        f"white spotlight beams with warm red accents, LOW HERO ANGLE so he looms, {WEAR}. A sleek "
        f"matte-black supercar sits behind him in the hazy red light. {PHOTO}"),
 "05": ([LEOP], f"An extreme close-up of the face of {LEO}, his pale chartreuse yellow-green eyes locking "
        f"the camera with a cool look; the brim of his backwards snapback and his gold chain visible at "
        f"the frame edge. Shallow focus. {PHOTO}"),
 "06": ([LEOP, RYAP], f"{LEO} (center, sharp) settled in a compact bread-loaf pose on a glowing lit "
        f"pedestal/throne under a single spotlight, gold chain and a clearly BACKWARDS snapback, regal. In "
        f"the soft-focus background {RYA} works a glowing DJ booth / turntables. Dark stage, red accents. {PHOTO}"),
 "07": ([LEOP], f"A WIDE LOW-ANGLE full-body shot of {LEO} standing foursquare on all fours in a cocky "
        f"B-boy stance on a dark spotlight stage, chin up, {WEAR}, gold chain swinging. Warm red rim "
        f"light. {PHOTO}"),
 "08": ([LEOP], f"A tight close insert: the two front PAWS of {LEO}, each wearing a small TAN TIMBERLAND "
        f"BOOT, kneading (꾹꾹이) down into a soft plush cushion. They are booted cat paws, not human "
        f"hands. Shallow focus, moody spotlight. {PHOTO}"),
 "10": ([LEOP], f"A tight cinematic shot of {LEO} as JOHN WICK — his head and the shoulders/collar of a "
        f"sharp black suit with a black tie, a sleek black pistol held up beside his head (barrel up). "
        f"Cropped at the upper chest (no full body). Cold dead-eyed stare, ears back, dark low-key "
        f"teal-amber moody lighting. {PHOTO}"),
 "11": ([LEOP], f"{LEO} seen in full dramatic SILHOUETTE (rim-lit outline, tail visible) standing on a "
        f"high ledge against a burning orange-gold sunrise, cinematic, backlit, moody. {PHOTO}"),
 "12": ([LEOP, RYAP], f"{LEO} and {RYA} as real four-legged pets in a confident idol formation inside a "
        f"cool deep-BLUE chamber with bright vertical neon light strips (NSYNC 'Bye Bye Bye' blue room). "
        f"Both in Y2K jersey fits + gold chains + Timberlands, BARE HEADS (no caps). They are COMPARABLE "
        f"in size. No people anywhere. {PHOTO}"),
 "13": ([LEOP, RYAP], f"{LEO} and {RYA} as real pets center-stage in a hero formation, a glowing neon "
        f"'LEO & RYANI' sign above them, floating golden coins drifting, a sleek supercar behind. Both "
        f"dressed in matching Y2K idol outfits + gold chains. Vivid bold colors. {PHOTO}"),
 "14": ([LEOP], f"{LEO} alone in a final hero pose, sitting tall and proud, lit by one dramatic spotlight "
        f"beam from above on a black stage, {WEAR}, a cool slow-blink expression. Iconic end-frame. {PHOTO}"),
}
NAME = {"01":"01_intro_wake","02":"02_entrance","03":"03_verse_glam","04":"04_catwalk","05":"05_green_eyes",
        "06":"06_loaf_throne","07":"07_timbs_insert","08":"08_knead","10":"10_consequences","11":"11_silhouette",
        "12":"12_chorus_blueroom","13":"13_chorus_value","14":"14_outro_freeze"}


def render(cut: str) -> None:
    imgs, prompt = B[cut]
    out = OUT / f"{NAME[cut]}_gpt.png"
    fhs = [open(p, "rb") for p in imgs]
    try:
        r = CLIENT.images.edit(model="gpt-image-2", image=fhs, prompt=prompt, size="1024x1536")
        out.write_bytes(base64.b64decode(r.data[0].b64_json))
        print("OK", out.name)
    except Exception as e:
        print("FAIL", cut, str(e)[:200])
    finally:
        for f in fhs: f.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    cuts = list(B) if a.all else (a.only.split(",") if a.only else [])
    if not cuts: ap.error("--only <01,04> or --all")
    for c in cuts: render(c.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
