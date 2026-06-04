"""
scripts/generate_stickers_ai.py
-------------------------------
Generate transparent PNG stickers using OpenAI's gpt-image-1 model
and drop them straight into `assets/stickers/{category}/` so the
sticker_scatter system picks them up on the next render.

Usage
-----
    # 8 heart stickers in a kawaii pastel style
    python3 scripts/generate_stickers_ai.py --category hearts --count 8 \
        --style "glossy kawaii sticker, pastel pink/red/gold hearts, thick white outline, soft shadow"

    # 5 Ryani & Leo signature character stickers
    python3 scripts/generate_stickers_ai.py --category rianileo --count 5 \
        --style "chibi sticker of a French bulldog (Ryani) and orange cat (Leo), kawaii"

    # Text label stickers — one sticker per phrase (count is ignored)
    python3 scripts/generate_stickers_ai.py --category labels \
        --text "happy day,enjoy,good vibes,sweet day,cozy time,best day,yay!,love love" \
        --style "3D puffy glossy pastel pill/banner/ribbon badge, kawaii cottagecore"

    # Show prompts without spending tokens
    python3 scripts/generate_stickers_ai.py --category sparkles --count 3 \
        --style "kawaii sparkle" --dry-run

Setup
-----
    pip install openai python-dotenv

    # Then create .env in the project root:
    #   OPENAI_API_KEY=sk-...

Cost (rough, 1024x1024)
-----------------------
    --quality low     ~$0.02 / sticker
    --quality medium  ~$0.04 / sticker  (default — good balance)
    --quality high    ~$0.17 / sticker  (only for hero/signature stickers)
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed.")
    print("  pip install openai python-dotenv")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in cwd or any parent
except ImportError:
    # dotenv optional — user may export OPENAI_API_KEY in shell instead
    pass

ROOT = Path(__file__).resolve().parent.parent
STICKERS_ROOT = ROOT / "assets" / "stickers"

# Per-category guidance fed into the prompt so model knows what the
# sticker subject should be. Keep this generic — style comes from --style.
CATEGORY_HINTS: dict[str, str] = {
    "hearts":   "a single heart sticker, centered, filling most of the canvas",
    "sparkles": (
        "a clean cute star/twinkle composition — pick ONE: "
        "a small cluster of 3-5 stars at varied sizes (not too many), "
        "a shooting star with a soft simple curved trail, "
        "a single bold rounded star with 2-3 smaller twinkles floating nearby, "
        "two stars side by side with a tiny sparkle accent between them, "
        "a classic puffy 4-point twinkle with a soft glow halo, "
        "or a single 5/6-point star with a small heart or twinkle accent. "
        "Centered. CLEAN and crisp shapes — NOT dense, NOT a glitter explosion, "
        "NOT sequin-like — think charming cute star decoration with breathing room"
    ),
    "paws":     "a single paw print sticker (4 toe beans + main pad), centered",
    "cute":     "a single small cute decoration (cherry blossom, daisy, ribbon bow, pastel butterfly, or fluffy cloud — pick one), centered",
    "closing":  "a single nighttime motif sticker (crescent moon, alarm clock, or shooting star), centered",
    "music":    "a single music note sticker (eighth note or beamed pair), centered",
    "weather":  "a single weather motif sticker (smiling sun, fluffy cloud, raindrop, rainbow arc, snowflake, or crescent moon with stars — pick one), centered",
    "cozy":     "a single cozy lifestyle object sticker (lit candle, open book, knitted mitten, fairy lights string, wool blanket, slippers, pillow, or steaming mug — pick one, no food items here), centered",
    "food": (
        "a single kawaii food sticker WITH a cute facial expression on it "
        "(eyes + small mouth; expression varies — pick one of: happy smile, "
        "blushing cheeks, sparkly love eyes, sleepy half-closed eyes, "
        "surprised round eyes, smug grin, content closed-eye smile). "
        "Pick ONE food shape: strawberry, peach, watermelon slice, croissant, "
        "donut with sprinkles, macaron, ice cream cone, slice of cake, "
        "bubble tea cup, ramen bowl, lollipop, pancake stack, cupcake, "
        "cherry pair, sushi piece, avocado, taiyaki, or melon bun — centered"
    ),
    "faces": (
        "a single kawaii emotion/face sticker on a simple rounded base "
        "(circle, cloud, heart, or rounded square) — pick ONE expression: "
        "happy with sparkly star eyes, love-struck with heart eyes, "
        "sleepy with z's and closed eyes, blushing with paws on cheeks, "
        "excited with wide open smile, content with soft eye-curve smile, "
        "surprised with big round eyes and small O mouth, smug, "
        "cozy with closed eyes and gentle smile, dreamy looking up, "
        "winking with tongue out — centered, expressive and very cute"
    ),
    "bubbles": (
        "a small rounded-square speech bubble sticker (with a little "
        "speech-bubble tail/pointer at the bottom-left or bottom-right corner) "
        "containing ONE simple icon inside: a tiny heart, small star, "
        "sparkle, exclamation mark, question mark, music note, paw print, "
        "tiny smiley, '...' dots, or a Z (sleep). The bubble has a clean "
        "pastel fill and crisp outline. The contained icon is centered "
        "inside the bubble. Centered on the canvas."
    ),
    "labels":   "a single text label sticker — a cute pill, banner, ribbon, or rounded badge shape with short text inside, centered",
    "ryani_face": (
        "a single kawaii chibi face sticker of Ryani — a small adorable "
        "black French bulldog (female, with classic bat ears standing up, "
        "big shiny dark eyes, short flat snout, a small white chest blaze peeking under chin, "
        "soft black fur). Just the HEAD/face from the front, no body. "
        "Cute expression varies per sticker — pick ONE: "
        "happy with sparkly star eyes and big smile, "
        "blushing with paws on cheeks, "
        "love-struck with heart eyes, "
        "sleepy with closed eyes and tiny Z floating above, "
        "playful with pink tongue sticking out, "
        "surprised with round wide eyes and small O mouth, "
        "content with soft eye-curve smile, "
        "winking with one eye closed. "
        "Centered, very cute and chibi-proportioned"
    ),
    "leo_face": (
        "a single kawaii chibi face sticker of Leo — a small adorable "
        "orange tabby kitten (with classic orange and cream tabby stripes "
        "on top of head and cheeks, bright green eyes, small pink nose, "
        "small triangular ears with inner pink, tiny white whiskers, "
        "soft fluffy fur). Just the HEAD/face from the front, no body. "
        "Cute expression varies per sticker — pick ONE: "
        "happy with sparkly green eyes and small smile, "
        "love-struck with heart eyes, "
        "curious with bright round wide eyes, "
        "sleepy with closed eyes and tiny Z floating above, "
        "mischievous winking with tongue out, "
        "blushing with content closed-eye smile, "
        "surprised with round eyes and small O mouth, "
        "playful with one ear flopped. "
        "Centered, very cute and chibi-proportioned"
    ),
    "rianileo": "a single chibi character sticker of Ryani (small black French bulldog) and Leo (small orange tabby kitten) together — friendship moment, both visible, centered",
}

# Categories where text IS desired inside the sticker (rendered by the model).
# Everything else gets a strict "no text" instruction.
TEXT_CATEGORIES: set[str] = {"labels"}

# Silhouette/finish variation cycles (decoupled from color so we can theme
# a batch around Ryani's pink-coral palette vs Leo's gold-orange palette).
SILHOUETTES: list[str] = [
    "plump rounded silhouette",
    "slightly elongated silhouette",
    "tilted ~15 degrees to the right",
    "tilted ~10 degrees to the left",
    "with extra glossy highlight on the upper left",
    "with a subtle inner gradient",
    "slightly larger and bolder than typical",
    "compact clean minimal silhouette",
    "with a delicate inner shine",
    "with a soft pastel gradient fill",
    "with a tiny sparkle accent on the corner",
    "with a glossy domed top",
]

# Color palettes — pick one via --color-theme (default 'all' = mixed pastels).
# Each entry becomes the "primary color" for one sticker in the batch.
COLOR_PALETTES: dict[str, list[str]] = {
    "all": [
        "soft pink", "peach", "butter yellow", "mint green",
        "baby blue", "soft lavender", "coral", "warm cream",
        "rose gold", "soft lilac", "dusty pink", "sage green",
    ],
    # Ryani — warm pinks, corals, blush. Use for cuts 1 (Ryani solo) or pairs
    # where you want the scatter to read as "Ryani's side".
    "ryani": [
        "soft pink", "coral pink", "peach", "rose gold",
        "blush pink", "warm pink", "dusty pink", "salmon",
        "raspberry", "magenta", "watermelon pink", "cherry red",
    ],
    # Leo — warm golds, ambers, oranges. Mirror palette for Leo-side scatters.
    "leo": [
        "butter yellow", "warm gold", "amber", "soft orange",
        "honey", "tangerine", "warm cream", "marigold",
        "apricot", "sunflower yellow", "caramel", "peach orange",
    ],
    # Cool — for closing/night cuts.
    "cool": [
        "baby blue", "soft lavender", "mint green", "sage green",
        "periwinkle", "powder blue", "soft teal", "lilac",
        "pale aqua", "dusty blue", "seafoam", "ice blue",
    ],
}


def build_prompt(category: str, style: str, silhouette: str, color: str,
                 text: str | None = None) -> str:
    subject = CATEGORY_HINTS.get(
        category, f"a single {category} sticker, centered"
    )

    if text and category in TEXT_CATEGORIES:
        text_part = (
            f' The badge prominently displays the text "{text}" '
            f'in a clean readable script or rounded sans-serif font. '
            f'Spell the text exactly as given, no typos. '
        )
        no_text_rule = ""
    else:
        text_part = ""
        no_text_rule = " No text, no letters, no watermark."

    variation = f"{silhouette}, primary color: {color} with soft cream highlight"

    return (
        f"{subject}. "
        f"Style: {style}. "
        f"Variation: {variation}."
        f"{text_part}"
        f" Crisp thick white outer outline and a soft drop shadow. "
        f"Pure transparent background — no scene, no frame, no border."
        f"{no_text_rule} "
        f"Sticker fills ~80% of the 1024x1024 canvas, fully visible, not cropped."
    )


def generate_one(client: OpenAI, prompt: str, quality: str) -> bytes:
    """Call gpt-image-1 and return raw PNG bytes."""
    resp = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        n=1,
        size="1024x1024",
        background="transparent",
        quality=quality,
    )
    return base64.b64decode(resp.data[0].b64_json)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate transparent PNG stickers via OpenAI gpt-image-1."
    )
    parser.add_argument("--category", required=True,
                        help="Subfolder under assets/stickers/ (e.g. hearts, sparkles)")
    parser.add_argument("--count", type=int, default=5,
                        help="How many stickers to generate (default 5)")
    parser.add_argument("--style", required=True,
                        help="Style fragment — e.g. 'glossy kawaii pastel hearts'")
    parser.add_argument("--quality", default="medium",
                        choices=["low", "medium", "high"],
                        help="Image quality / cost tier (default medium)")
    parser.add_argument("--prefix", default=None,
                        help="Filename prefix (default: <category>_ai or "
                             "<category>_<theme>_ai when --color-theme is set)")
    parser.add_argument("--text", default=None,
                        help="For 'labels' category: comma-separated phrases "
                             "(one sticker per phrase, --count is ignored)")
    parser.add_argument("--color-theme", default="all",
                        choices=list(COLOR_PALETTES.keys()),
                        help="Color palette for the batch: "
                             "'all' (mixed pastels, default), "
                             "'ryani' (pink/coral/blush), "
                             "'leo' (gold/amber/orange), "
                             "'cool' (blue/lavender/mint)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts only, no API calls")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_path = ROOT / ".env"
        print("ERROR: OPENAI_API_KEY not found in env or .env file.")
        print(f"  Create {env_path} with:")
        print(f"    OPENAI_API_KEY=sk-...")
        return 1

    out_dir = STICKERS_ROOT / args.category
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prefix carries the theme name when not 'all' so files are filterable
    # later by name (e.g. hearts/hearts_ryani_ai_*.png).
    if args.prefix:
        prefix = args.prefix
    elif args.color_theme == "all":
        prefix = f"{args.category}_ai"
    else:
        prefix = f"{args.category}_{args.color_theme}_ai"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    palette = COLOR_PALETTES[args.color_theme]

    # Build the work list: each entry is (index, text_or_None, filename_suffix)
    if args.text:
        texts = [t.strip() for t in args.text.split(",") if t.strip()]
        work = [(i, t, _slug(t)) for i, t in enumerate(texts)]
    else:
        work = [(i, None, f"{i+1:02d}") for i in range(args.count)]

    total = len(work)

    print(f"Category : {args.category}")
    print(f"Count    : {total}")
    print(f"Quality  : {args.quality}")
    print(f"Theme    : {args.color_theme} ({len(palette)} colors)")
    print(f"Style    : {args.style}")
    if args.text:
        print(f"Texts    : {args.text}")
    print(f"Output   : assets/stickers/{args.category}/")
    print()

    if args.dry_run:
        for i, text, suffix in work:
            silhouette = SILHOUETTES[i % len(SILHOUETTES)]
            color = palette[i % len(palette)]
            prompt = build_prompt(args.category, args.style, silhouette,
                                  color, text=text)
            print(f"[{i+1}/{total}] {prefix}_{ts}_{suffix}.png  ({color})")
            print(f"  prompt: {prompt}")
            print()
        print("Dry run complete. No API calls made.")
        return 0

    client = OpenAI(api_key=api_key)
    saved = 0
    failed = 0

    for i, text, suffix in work:
        silhouette = SILHOUETTES[i % len(SILHOUETTES)]
        color = palette[i % len(palette)]
        prompt = build_prompt(args.category, args.style, silhouette,
                              color, text=text)
        out_path = out_dir / f"{prefix}_{ts}_{suffix}.png"
        label = f' "{text}"' if text else ""
        print(f"[{i+1}/{total}] {out_path.name}  [{color}]{label}")

        try:
            png_bytes = generate_one(client, prompt, args.quality)
            out_path.write_bytes(png_bytes)
            kb = len(png_bytes) // 1024
            print(f"           saved ({kb} KB)")
            saved += 1
        except Exception as e:
            print(f"           FAILED: {e}")
            failed += 1

        # Gentle pacing for rate limits
        if i < total - 1:
            time.sleep(0.6)

    print()
    print(f"Done. saved={saved}  failed={failed}  -> assets/stickers/{args.category}/")
    return 0 if failed == 0 else 2


def _slug(text: str, maxlen: int = 24) -> str:
    """Filesystem-safe slug from a phrase."""
    cleaned = "".join(c if c.isalnum() else "_" for c in text.lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:maxlen] or "label"


if __name__ == "__main__":
    sys.exit(main())
