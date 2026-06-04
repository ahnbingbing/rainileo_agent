"""
agents/room_extractor.py — Phase I: VLM-driven 3D room layout extraction.

Built 2026-06-01 after PD's well-justified frustration: I had been asking
PD to specify each furniture piece manually while 67 background photos +
a hand-drawn floor plan + recent home videos already encoded the layout.

What it does:
1. Gather: PD's floor plan + all home-tagged background photos + recent
   home video keyframes (the rich visual evidence base).
2. Single multimodal call to Gemini 2.5 Pro (chosen for spatial reasoning
   over Flash). Returns a structured JSON describing each room's walls,
   anchors with positions and approximate dimensions, and inter-room
   connections.
3. Convert that JSON into Python coordinate constants that the existing
   Blender build script (`assets/3d/scripts/build_grandma_livingroom.py`)
   already speaks.
4. Write a generated `assets/3d/scripts/vlm_layout.py` that the build
   script reads, so the script's hand-coded constants get replaced with
   VLM-extracted ones.
5. PD reviews the next render, marks ✗, the cycle re-runs with their
   notes appended.

Run:
    python3 -m agents.room_extractor
        --photos-dir assets/backgrounds
        --floor-plan assets/backgrounds/pd_floor_plan_grandma.jpg
        --videos-from-db
        --out assets/3d/scripts/vlm_layout.py

Cost: ~$0.30-0.60 on Gemini 2.5 Pro (long-context multimodal).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("room_extractor")
DB_PATH = ROOT / "data" / "agent.db"

SYSTEM_PROMPT = """\
You are the "3D Room Mapper" for the Ryani & Leo YouTube Shorts channel.
Your job is to look at a Korean grandmother's house — a hand-drawn floor
plan + many real photos + a few recent video keyframes — and emit a single
JSON describing the 3D layout. This JSON becomes the source of truth for a
Blender model that anchors all video renders.

You must:
- Treat the floor plan as the geometric ground truth for room shapes and
  door positions. Photos confirm + add furniture, materials, and exact
  positions within rooms.
- Use cardinal directions (NORTH / SOUTH / EAST / WEST) for every anchor.
- Give approximate dimensions in METERS when you can infer them from visual
  scale (chairs are ~0.45m wide, doors ~0.9m wide, ceilings ~2.7m, etc.).
- When evidence is thin, mark `confidence: "low"` and explain in a note —
  don't fabricate.
- Prefer recent photos (2025-10+) over older ones when they conflict.

Output schema (exactly this top-level structure):
{
  "rooms": {
    "<room_id>": {
      "korean_name": string,
      "shape_rect": { "width_m": float, "depth_m": float, "height_m": float },
      "walls": {
        "NORTH": { "anchors": [Anchor], "doors": [Door], "windows": [Window] },
        "SOUTH": { ... },
        "EAST":  { ... },
        "WEST":  { ... }
      },
      "floor_material": string,
      "wall_material": string,
      "ceiling_material": string,
      "notable_freestanding": [Anchor],
      "confidence": "high" | "medium" | "low",
      "evidence_photos": [string]  // 2-5 most relevant photo filenames
    }
  },
  "room_connections": [
    { "from": "<room_id>", "to": "<room_id>", "via": "door|opening",
      "wall_of_from": "NORTH|SOUTH|EAST|WEST", "width_m": float }
  ],
  "global_notes": [string]
}

Where:
  Anchor = {
    "name_ko": string,
    "name_en": string,
    "category": "furniture" | "appliance" | "decor" | "fixture",
    "wall": "NORTH" | "SOUTH" | "EAST" | "WEST" | "FREESTANDING",
    "position_along_wall_pct": float,  // 0=west/south end, 1=east/north end
    "depth_from_wall_m": float,        // 0 if against wall
    "dim_w_m": float, "dim_d_m": float, "dim_h_m": float,
    "material_hint": string,
    "confidence": "high" | "medium" | "low",
    "notes": string
  }
  Door = { "wall_pos_pct": float, "width_m": float, "leads_to_room": string|null }
  Window = { "wall_pos_pct": float, "width_m": float, "sill_height_m": float, "type": "regular|frosted_high|sliding" }

Focus on the LIVING ROOM (거실) and the KITCHEN/DINING area for this pass —
those are the cuts the channel renders most. Other rooms (bedrooms, baths)
may be brief or omitted.

Return ONLY the JSON. No prose, no markdown fences.
"""


def gather_photos(photos_dir: Path, max_n: int = 28) -> list[Path]:
    """Return up to max_n background photos likely showing the home."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT file_path, space_name FROM background_refs ORDER BY id"
    ).fetchall()
    paths: list[Path] = []
    seen: set[Path] = set()
    for r in rows:
        fp = Path(r["file_path"])
        if not fp.is_absolute():
            fp = ROOT / fp
        if fp.exists() and fp not in seen:
            paths.append(fp)
            seen.add(fp)
            if len(paths) >= max_n:
                break
    return paths


def gather_video_keyframes(max_videos: int = 4,
                            frames_per_video: int = 2) -> list[Path]:
    """Extract a couple of keyframes from each of the most recent home videos."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT file_path FROM assets WHERE kind='video' AND location_type='home' "
        "AND captured_iso > '2025-09-01' AND vlm_analyzed_at IS NOT NULL "
        "ORDER BY captured_iso DESC LIMIT ?", (max_videos,)
    ).fetchall()
    out_dir = Path(tempfile.mkdtemp(prefix="vlm_keyframes_"))
    keyframes: list[Path] = []
    for r in rows:
        v = Path(r["file_path"])
        if not v.is_absolute():
            v = ROOT / v
        if not v.exists():
            continue
        try:
            dur_str = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(v)],
                capture_output=True, text=True, check=True, timeout=15,
            ).stdout.strip()
            dur = float(dur_str)
        except Exception:
            continue
        for i in range(frames_per_video):
            t = dur * (i + 0.5) / frames_per_video
            out = out_dir / f"{v.stem}_t{t:.1f}s.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(v),
                 "-frames:v", "1", "-q:v", "5", str(out)],
                capture_output=True, timeout=30,
            )
            if out.exists() and out.stat().st_size > 5000:
                keyframes.append(out)
    return keyframes


def call_gemini(floor_plan: Path, photos: list[Path],
                 keyframes: list[Path]) -> dict:
    import google.generativeai as genai
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-pro",
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.3,
            "response_mime_type": "application/json",
        },
    )
    from PIL import Image
    parts = ["The FLOOR PLAN below is the geometric ground truth. After it, "
             "real photos from the same home — use them to fill in materials, "
             "furniture, and exact positions within rooms.\n"]
    parts.append("[FLOOR PLAN]")
    parts.append(Image.open(floor_plan))
    parts.append(f"\n[{len(photos)} REAL PHOTOS]")
    for p in photos:
        parts.append(Image.open(p))
    if keyframes:
        parts.append(f"\n[{len(keyframes)} RECENT VIDEO KEYFRAMES]")
        for k in keyframes:
            parts.append(Image.open(k))
    parts.append(
        "\nNow emit the room-layout JSON per the schema in your system prompt."
    )
    resp = model.generate_content(parts)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def layout_to_blender_constants(layout: dict) -> str:
    """Render the extracted layout as a Python module the build script imports.
    This sits at assets/3d/scripts/vlm_layout.py and overrides the hand-coded
    constants when present.
    """
    lines = [
        '"""Auto-generated by agents/room_extractor.py — DO NOT HAND-EDIT.',
        f"   Re-run `python -m agents.room_extractor` to refresh.",
        '"""',
        "from __future__ import annotations",
        "",
        "ROOMS = " + json.dumps(layout.get("rooms", {}), ensure_ascii=False, indent=2),
        "",
        "ROOM_CONNECTIONS = " + json.dumps(layout.get("room_connections", []),
                                            ensure_ascii=False, indent=2),
        "",
        "GLOBAL_NOTES = " + json.dumps(layout.get("global_notes", []),
                                        ensure_ascii=False, indent=2),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--photos-dir", default="assets/backgrounds")
    p.add_argument("--floor-plan",
                   default="assets/backgrounds/pd_floor_plan_grandma.jpg")
    p.add_argument("--max-photos", type=int, default=28)
    p.add_argument("--videos-from-db", action="store_true",
                   help="Also pull recent home video keyframes from DB")
    p.add_argument("--out-py",
                   default="assets/3d/scripts/vlm_layout.py")
    p.add_argument("--out-json",
                   default="assets/3d/scripts/vlm_layout.json")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan + count only, no Gemini call")
    args = p.parse_args()

    floor_plan = ROOT / args.floor_plan
    if not floor_plan.exists():
        print(f"ERROR: floor plan not found: {floor_plan}", file=sys.stderr)
        return 2

    photos = gather_photos(ROOT / args.photos_dir, args.max_photos)
    log.info("photos: %d", len(photos))
    keyframes: list[Path] = []
    if args.videos_from_db:
        keyframes = gather_video_keyframes()
        log.info("video keyframes: %d", len(keyframes))

    if args.dry_run:
        print(f"[dry] would send floor_plan + {len(photos)} photos + "
              f"{len(keyframes)} keyframes to Gemini 2.5 Pro")
        return 0

    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        return 2

    log.info("calling Gemini 2.5 Pro...")
    layout = call_gemini(floor_plan, photos, keyframes)

    out_json = ROOT / args.out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(layout, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    log.info("wrote %s (%d rooms, %d connections)",
             out_json, len(layout.get("rooms", {})),
             len(layout.get("room_connections", [])))

    out_py = ROOT / args.out_py
    out_py.write_text(layout_to_blender_constants(layout), encoding="utf-8")
    log.info("wrote %s", out_py)

    # Print a short summary so PD can see what was extracted at a glance
    print("\n=== rooms extracted ===")
    for rid, room in layout.get("rooms", {}).items():
        ws = room.get("shape_rect", {})
        print(f"  · {rid} ({room.get('korean_name','?')}) "
              f"{ws.get('width_m','?')}m × {ws.get('depth_m','?')}m × "
              f"{ws.get('height_m','?')}m  conf={room.get('confidence','?')}")
        for wall, info in (room.get("walls") or {}).items():
            anchors = info.get("anchors", [])
            if anchors:
                names = ", ".join(a.get("name_ko", "?") for a in anchors[:5])
                print(f"      {wall}: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
