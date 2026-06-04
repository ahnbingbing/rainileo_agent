"""
scripts/process_card_stickers.py
--------------------------------
Read a Concept Card JSON file, extract its `sticker_additions` array,
and generate the requested stickers by invoking generate_stickers_ai.py
for each entry.

This is the bridge between the Writer Agent (which declares "I need
maple leaf stickers for tomorrow's autumn walk concept") and the
Cameraman Agent (which renders the video). Run it after PD approval,
before render.

Usage
-----
    # Process a specific card
    python3 scripts/process_card_stickers.py data/concept_cards/2026-05-15.json

    # Dry run — show what would be generated, no API calls
    python3 scripts/process_card_stickers.py data/concept_cards/2026-05-15.json --dry-run

    # Skip categories that already have N+ AI files (avoid re-generation)
    python3 scripts/process_card_stickers.py CARD.json --skip-existing 8

Exit codes
----------
    0   : all additions generated successfully (or none requested)
    2   : at least one addition failed
    3   : card JSON invalid or missing required keys
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STICKERS_ROOT = ROOT / "assets" / "stickers"
GENERATOR = ROOT / "scripts" / "generate_stickers_ai.py"


def count_existing_ai(category: str) -> int:
    """How many *_ai_*.png files already live in this category folder?"""
    cat_dir = STICKERS_ROOT / category
    if not cat_dir.is_dir():
        return 0
    return sum(1 for p in cat_dir.glob("*_ai_*.png"))


def build_cli(addition: dict, dry_run: bool) -> list[str]:
    """Translate one sticker_additions entry into a generate_stickers_ai.py call."""
    cmd = [
        sys.executable, str(GENERATOR),
        "--category", addition["category"],
        "--style", addition["style"],
    ]

    if addition.get("text"):
        cmd += ["--text", addition["text"]]
    else:
        cmd += ["--count", str(addition.get("count", 5))]

    theme = addition.get("color_theme")
    if theme and theme != "all":
        cmd += ["--color-theme", theme]

    if dry_run:
        cmd += ["--dry-run"]

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate stickers requested by a Concept Card."
    )
    parser.add_argument("card_path",
                        help="Path to the Concept Card JSON file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show planned generation, no API calls")
    parser.add_argument("--skip-existing", type=int, default=0,
                        metavar="N",
                        help="Skip a category if it already has N or more "
                             "AI-generated files (default 0 = never skip)")
    args = parser.parse_args()

    card_path = Path(args.card_path)
    if not card_path.exists():
        print(f"ERROR: card not found: {card_path}", file=sys.stderr)
        return 3

    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {card_path}: {e}", file=sys.stderr)
        return 3

    additions = card.get("sticker_additions") or []
    if not isinstance(additions, list):
        print(f"ERROR: sticker_additions must be array or null", file=sys.stderr)
        return 3

    print("=" * 60)
    print(f"Card: {card.get('card_id', '?')}")
    print(f"Date: {card.get('date', '?')}")
    print(f"Theme: {card.get('theme', '?')}")
    print(f"Sticker additions: {len(additions)}")
    print("=" * 60)

    if not additions:
        print("No sticker additions requested. Card relies on base library.")
        return 0

    failures: list[tuple[int, str]] = []

    for idx, addition in enumerate(additions, start=1):
        cat = addition.get("category", "?")
        rationale = addition.get("rationale", "(no rationale)")
        print()
        print(f">>> [{idx}/{len(additions)}] {cat}")
        print(f"    reason : {rationale}")

        # Skip-existing check
        existing = count_existing_ai(cat)
        if args.skip_existing and existing >= args.skip_existing:
            print(f"    skip   : {existing} AI files already exist "
                  f"(>= --skip-existing {args.skip_existing})")
            continue

        cmd = build_cli(addition, args.dry_run)
        print(f"    cmd    : {' '.join(cmd[2:])}")  # hide python path

        try:
            result = subprocess.run(cmd, cwd=ROOT)
            if result.returncode != 0:
                failures.append((idx, cat))
                print(f"    !! generator exited with code {result.returncode}")
        except Exception as e:
            failures.append((idx, cat))
            print(f"    !! failed: {e}")

    print()
    print("=" * 60)
    if failures:
        print(f"FAILED batches: {len(failures)}")
        for idx, cat in failures:
            print(f"  - [{idx}] {cat}")
        print()
        print("Tip: re-run this same command. New files have fresh timestamps")
        print("so successful batches won't be overwritten.")
        return 2
    print(f"All {len(additions)} sticker batches generated.")
    print(f"Ready for render: python3 scripts/render_episode_N.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
