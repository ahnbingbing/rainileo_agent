#!/usr/bin/env python3
"""
build_cut_prompts.py
====================
Regenerates all 5 per-cut prompt files for Episode 1, each with a distinct
overlay theme so cuts don't look visually identical.

Two important rules baked into every prompt:
  1. NO TEXT of any kind in the image — captions are added downstream as
     real subtitles (so Korean glyphs don't get mangled).
  2. NO stickers on the pets — only in negative space. If there isn't
     enough room, use fewer stickers rather than overlapping the pets.

Single source of truth — edit CUTS below to tweak any cut, then re-run:

    python3 scripts/build_cut_prompts.py

Also writes:
    scripts/prompts/captions.json   — caption text per cut, for the
                                      later subtitle-overlay step.
"""

import json
from pathlib import Path

BASE_STYLE = """Edit the provided pet photo.

Keep the original photo realistic.
Do not repaint, cartoonize, or stylize the pets themselves.
Do not transform the whole image into an illustration.
Preserve the real-life look of the pets and background.

NO TEXT IN THE IMAGE.
Do not add any letters, words, captions, speech bubbles, Korean characters,
English characters, or written symbols of any language. Stickers only — no
writing of any kind anywhere in the image. Captions will be added later as
separate subtitles outside this step.

Only add decorative overlays in a modern Korean webtoon reaction style.

Style:
- cute, playful, affectionate
- clean black outlines
- pink heart accents
- yellow/white sparkles
- expressive but polished
- not scrapbook style
- not random emoji clutter

Placement (STRICT — this is the most important rule):
- NEVER place any sticker on the pets' faces, eyes, ears, fur, paws, or bodies.
- ALL stickers must sit in clean negative space (sky, floor, blank wall, empty corners, background).
- Keep a clear margin between every sticker and the pets — stickers do not touch the pets at any point.
- If there is not enough clean negative space for the listed stickers, USE FEWER stickers. Do not overlap the pets to make room.
- The pets are the focus; stickers are accents around them, never on them.

Distribution across the frame:
- Spread the stickers across BOTH the upper AND the lower half of the frame. Do not cluster everything at the top.
- Treat the floor / lower corners / lower-side empty areas as valid negative space and place at least some stickers there too.
- Aim for stickers in the upper third, the side margins, AND the lower third whenever clean space exists in those regions.
- Avoid leaving any large empty zone visually bare (especially the bottom of the frame)."""


# ---- Per-cut decoration themes ---------------------------------------------
# Each cut has its own theme so the 5 outputs feel visually different.
# `include` = the EXACT set of overlays for this cut (no text items).
# `exclude` = explicit anti-list so the AI doesn't default to repeats.
# `caption`  = Korean caption text, recorded here only for the downstream
#              subtitle step. It is NOT injected into the AI prompt.

CUTS = [
    dict(
        tag="cut1_ryani_hook",
        caption="오늘도 귀여움 과다",
        theme="SPARKLE BURST (opening hook — grab attention, not a love cut)",
        include=[
            "4 to 5 yellow 6-point sparkles SPREAD across the frame — some in the upper area around Ryani's head, AND at least 1 to 2 in the lower negative space (floor / lower-corners), all in empty space never touching her fur or face",
            "1 short cluster of small black action / excitement lines in empty space off to one side of her head",
            "1 small pink heart in a corner of the frame (upper OR lower corner — pick one, but if upper is busy use a lower corner)",
        ],
        exclude=[
            "halo (save for a later cut)",
            "paw prints",
            "blush marks",
            "many hearts — this is a sparkly hook, not a love cut",
            "ANY text, letters, words, or speech bubbles",
        ],
    ),
    dict(
        tag="cut2_leo_intro",
        caption="레오도 질 수 없지",
        theme="ATTENTION BURST (Leo's entrance — punchy, slight rival energy)",
        include=[
            "2 small white 4-point sparkles in negative space — 1 in the upper area near Leo, 1 in the lower negative space (floor / lower-corner), neither on his body",
            "1 small yellow star shape in upper negative space near him",
            "1 short pair of black action lines in empty space on each side of his head (entrance emphasis)",
            "2 tiny black paw-print silhouettes in the lower negative space / floor area (not on Leo)",
        ],
        exclude=[
            "pink hearts (this is not a love cut)",
            "halo",
            "blush marks",
            "a glittery sparkle field — keep it punchy and dry, not romantic",
            "ANY text, letters, words, or speech bubbles",
        ],
    ),
    dict(
        tag="cut3_together_play",
        caption="둘이 있으면 더 귀여움",
        theme="PLAYFUL ENERGY (paw prints + motion lines)",
        include=[
            "3 to 4 small black paw-print silhouettes scattered in the LOWER half of the frame — on empty floor / lower-corner area (never on the pets). These MUST appear in the lower portion of the image, not in the upper area.",
            "1 cluster of black motion / excitement lines in the upper negative space ABOVE the pets — never between or on their faces",
            "2 small yellow sparkles — 1 in upper negative space, 1 in lower negative space",
        ],
        exclude=[
            "halo (save for cut 4)",
            "pink heart shower (save for cut 4)",
            "blush marks",
            "ANY text, letters, words, or speech bubbles",
        ],
    ),
    dict(
        tag="cut4_together_warm",
        caption="이젠 단짝",
        theme="HEART STORM + HALO (warm, bonded duo)",
        include=[
            "4 to 5 pink hearts of varied sizes scattered across the frame — at least 2 in the upper negative space ABOVE the pets, and at least 1 to 2 in the lower negative space (floor / lower-corners). Never on their faces, ears, or fur.",
            "1 small yellow halo ring floating in the empty space directly above the two pets, centered over their group (above their heads, not on them)",
            "2 small yellow sparkles — 1 in upper negative space, 1 in lower negative space",
        ],
        exclude=[
            "paw prints",
            "black action / motion lines",
            "a loud confetti vibe — keep this cut soft and warm",
            "blush marks (no stickers on faces)",
            "ANY text, letters, words, or speech bubbles",
        ],
    ),
    dict(
        tag="cut5_closer",
        caption="오늘의 귀여움 완료",
        theme="CONFETTI WRAP (finale flourish — different sticker shapes than earlier cuts)",
        include=[
            "6 to 8 small sparkles scattered like confetti ACROSS the FULL frame — mix of yellow 6-point, white 4-point, and small pink dots. Distribute evenly between the upper third, the middle side-margins, AND the lower third of the frame — do not cluster them all at the top.",
            "1 small pink heart in negative space (just one, as a finishing touch)",
            "1 small star shape (distinct from the sparkles) in negative space",
            "1 small check-mark symbol in an empty lower corner of the frame",
        ],
        exclude=[
            "halo",
            "paw prints",
            "black action / motion lines",
            "ANY text, letters, words, or speech bubbles",
        ],
    ),
]


def build_one(cut: dict) -> str:
    lines = [BASE_STYLE, ""]
    lines.append(f"Overlay focus for this cut — theme: {cut['theme']}.")
    lines.append("Use ONLY the following overlays (do not add extras from outside this list):")
    for item in cut["include"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("AVOID for this cut (do NOT add):")
    for item in cut["exclude"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        "Final reminder: ZERO text/letters in the image. The image must "
        "contain only the pets and the listed overlay shapes — no written "
        "characters anywhere."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "prompts"
    out_dir.mkdir(exist_ok=True)
    for cut in CUTS:
        path = out_dir / f"edit_v3_{cut['tag']}.txt"
        path.write_text(build_one(cut), encoding="utf-8")
        print(f"wrote {path}")

    # Export captions JSON for the later subtitle-overlay step.
    captions = {cut["tag"]: cut["caption"] for cut in CUTS}
    captions_path = out_dir / "captions.json"
    captions_path.write_text(
        json.dumps(captions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {captions_path}")


if __name__ == "__main__":
    main()
