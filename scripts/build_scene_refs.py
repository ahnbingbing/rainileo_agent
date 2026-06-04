"""
scripts/build_scene_refs.py
---------------------------
Build the canonical scene reference image library: one empty-of-pets photo
per set_anchor in data/set_library.json.

The result lives at `assets/scene_refs/<set_anchor>.png` and is referenced
by `cameraman._generate_scene_ref()` so every ai_vtuber episode that uses
the same set_anchor passes Seedance the SAME background reference — no
more per-episode background drift.

Pipeline per set:
  1. Query DB for the highest-quality, properly-lit photo whose
     `location_type` + `background` match the set_anchor.
  2. Use OpenAI gpt-image-1 (images.edit) with that photo as the source,
     prompting "remove all animals and people, keep the empty room."
  3. Save to assets/scene_refs/<set_anchor>.png.
  4. Append `scene_ref` field to set_library.json entry.

Idempotent: skips set_anchors whose output already exists unless --force.

Usage:
    # Build all primary set_anchors (default selection)
    python3 scripts/build_scene_refs.py

    # Single set
    python3 scripts/build_scene_refs.py --set home_livingroom

    # Force regenerate
    python3 scripts/build_scene_refs.py --set home_bedroom --force

    # Dry-run (no API)
    python3 scripts/build_scene_refs.py --dry-run

Cost: ~$0.05-0.10 per set on gpt-image-1 standard quality.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("build_scene_refs")
DB_PATH = ROOT / "data" / "agent.db"
SET_LIBRARY_PATH = ROOT / "data" / "set_library.json"
OUT_DIR = ROOT / "assets" / "scene_refs"

# Default selection — the most-used sets we want canonical refs for.
DEFAULT_SETS = [
    "home_livingroom",
    "home_bedroom",
    "home_kitchen",
    "home_bathroom",
    "mom_livingroom",
    "cafe_table_area",
    "cafe_sofa_area",
]

EDIT_PROMPT = (
    "Remove all animals (dogs, cats, kittens) and all people from this image. "
    "Keep only the empty room as it would look without any living creatures present. "
    "PRESERVE EXACTLY: all furniture (sofa, table, bed, cabinets), walls, wallpaper, "
    "floor type and color, window position and size, curtains and their color, "
    "lighting and time of day, decorations, plants, rugs, all visible objects. "
    "Output a clean photographic image of the empty room from the same camera angle. "
    "No animals. No people. Just the space."
)


def pick_source_photo(con: sqlite3.Connection, set_anchor: str, lib_entry: dict) -> Path | None:
    """Find the best candidate photo for this set_anchor from the DB."""
    loc_type = lib_entry.get("location_type")
    bgs = lib_entry.get("backgrounds", [])
    if not loc_type:
        log.warning("set_library[%s] has no location_type — skip", set_anchor)
        return None

    # Build the SQL: prefer high quality, photo kind, matching location_type
    # and one of the listed backgrounds, no HEIC, no decoration.
    bg_clause = ""
    params: list = [loc_type]
    if bgs:
        bg_marks = ",".join(["?"] * len(bgs))
        bg_clause = f"AND background IN ({bg_marks})"
        params.extend(bgs)

    rows = con.execute(
        f"""
        SELECT asset_id, file_path, quality_score, background, scene_description
        FROM assets
        WHERE kind='photo' AND vlm_analyzed_at IS NOT NULL
          AND location_type = ?
          {bg_clause}
          AND quality_score >= 0.7
          AND file_path NOT LIKE '%.heic'
          AND (decoration_level IS NULL OR decoration_level='none')
        ORDER BY quality_score DESC, captured_iso DESC
        LIMIT 10
        """,
        params,
    ).fetchall()
    if not rows:
        log.warning("no candidate photos for %s (loc=%s, bgs=%s)", set_anchor, loc_type, bgs)
        return None
    # Pick the highest scoring one. Could improve: prefer photos with fewer
    # subjects (multiple pets vs single etc.), but quality_score already
    # correlates with clean composition.
    chosen = Path(rows[0][1])
    log.info("source for %s: q=%.2f bg=%s | %s",
             set_anchor, rows[0][2], rows[0][3], chosen.name)
    return chosen


def _build_square_source(src: Path, tmp_png: Path) -> Path:
    """Crop/resize to 1024x1024 PNG for OpenAI images.edit."""
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


def generate_scene_ref(set_anchor: str, source_photo: Path, out_path: Path,
                       dry_run: bool = False, mode: str = "edit") -> bool:
    """Produce the scene_ref via one of two modes.

    mode='edit' — Use OpenAI gpt-image-1 to remove pets/people while preserving
        the room. Risk: model may reimagine architectural details, losing
        the actual home's character (sofa shape, knickknacks, wall details).
    mode='copy' — Just copy the source photo verbatim (square-cropped to 1024).
        Pets stay in the image but character_ref images override character
        identity at Seedance call time. Best for preserving real home detail.
    """
    if dry_run:
        log.info("[dry-run] mode=%s, would write %s → %s",
                 mode, source_photo.name, out_path)
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "copy":
        # Square-crop the source photo verbatim and save as the scene_ref.
        _build_square_source(source_photo, out_path)
        log.info("  copied %s → %s (%d KB)",
                 source_photo.name, out_path.name,
                 out_path.stat().st_size // 1024)
        return True

    # mode == "edit"
    from openai import OpenAI
    client = OpenAI()
    tmp_png = ROOT / "data" / "tmp" / f"_sceneref_src_{set_anchor}.png"
    _build_square_source(source_photo, tmp_png)
    log.info("Editing scene ref for %s (prompt %d chars)", set_anchor, len(EDIT_PROMPT))
    result = client.images.edit(
        model="gpt-image-1",
        image=open(tmp_png, "rb"),
        prompt=EDIT_PROMPT,
        size="1024x1024",
        quality="high",
        n=1,
    )
    png_bytes = base64.b64decode(result.data[0].b64_json)
    out_path.write_bytes(png_bytes)
    log.info("  wrote %s (%d KB)", out_path, len(png_bytes) // 1024)
    return True


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--set", action="append", default=[],
                   help="set_anchor to build (repeatable). Default: primary list.")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing scene_ref.png")
    p.add_argument("--mode", choices=["edit", "copy"], default="copy",
                   help="copy (default, preserves real home detail; pets stay in ref but character refs override at Seedance) "
                        "vs edit (GPT removes pets, but may reimagine architecture)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    selected = args.set or DEFAULT_SETS
    library = json.loads(SET_LIBRARY_PATH.read_text(encoding="utf-8"))
    unknown = [s for s in selected if s not in library]
    if unknown:
        print(f"ERROR: unknown set_anchor(s): {unknown}\nKnown: {list(library.keys())}",
              file=sys.stderr)
        return 2

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    plan: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for s in selected:
        out = OUT_DIR / f"{s}.png"
        if out.exists() and not args.force:
            skipped.append(s)
            continue
        src = pick_source_photo(con, s, library[s])
        if not src:
            print(f"  skip {s} (no source)", file=sys.stderr)
            continue
        plan.append((s, src))

    print(f"Plan: build {len(plan)} scene refs, skip {len(skipped)} existing")
    for s, src in plan:
        print(f"  → {s}  source={src.name}")
    if skipped:
        print(f"  skip: {', '.join(skipped)} (use --force to overwrite)")

    if args.dry_run or not plan:
        print("[dry-run done]" if args.dry_run else "Nothing to do.")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    # Generate
    for s, src in plan:
        out = OUT_DIR / f"{s}.png"
        try:
            generate_scene_ref(s, src, out, mode=args.mode)
        except Exception as e:
            log.exception("Failed %s", s)
            print(f"ERROR: {s} failed: {e}", file=sys.stderr)
            continue

    # Update set_library.json with scene_ref paths
    for s in selected:
        out = OUT_DIR / f"{s}.png"
        if out.exists():
            library[s]["scene_ref"] = str(out.relative_to(ROOT))
    SET_LIBRARY_PATH.write_text(
        json.dumps(library, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nUpdated {SET_LIBRARY_PATH.name} with scene_ref paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
