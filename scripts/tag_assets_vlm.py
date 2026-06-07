"""
scripts/tag_assets_vlm.py — Bulk VLM analysis of all assets.

Sends each photo to Gemini Flash vision and writes rich metadata to the DB.
For videos, extracts a representative frame first.

Usage:
    python3 scripts/tag_assets_vlm.py                    # all untagged
    python3 scripts/tag_assets_vlm.py --limit 50         # first 50
    python3 scripts/tag_assets_vlm.py --force             # re-analyze all
    python3 scripts/tag_assets_vlm.py --asset med_2026... # single asset
    python3 scripts/tag_assets_vlm.py --dry-run           # test prompt, no DB write

Cost: ~$0.003/photo (Gemini 2.5 Flash), ~$7.5 for 2500 assets.
Time: ~1-2 hours for full batch (rate limited).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("tag_assets_vlm")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
TMP_DIR = ROOT / "data" / "tmp" / "vlm_frames"

ANALYSIS_PROMPT = """\
You are analyzing a photo/frame from a pet YouTube Shorts channel.
The channel features two pets:
- **Ryani** (랴니): black French Bulldog, white markings on chin/chest/paws, NO tail (NEVER hallucinate a tail on her), spayed female, 11 years old, calm/wise.
- **Leo** (레오): orange tabby cat, ~8 months, male, prankster.

Your description will be used by a Writer agent to author episode storyboards. The Writer trusts this output as ground truth — be FACTUAL, do not embellish, do not assume. If something is unclear, say so.

Return ONLY valid JSON with these fields:

{
  "scene_description": "Korean factual description, 3-5 sentences. Cover (a) where this is (specific room/area, not just 'kitchen'), (b) what each visible subject is doing right now, (c) what's in the immediate surroundings (props/furniture), (d) what direction the subject is facing/looking, (e) anything notable like leashes/harnesses/hands holding the pet. Avoid narrator embellishment — no '편안하게', '호기심 어린' unless directly evidenced. If you see a door, say it's a door. If you see grass/풀 the cat is eating, say cat grass.",
  "subjects_visible": ["ryani", "leo", "human"],
  "focus_subject": "ryani" | "leo" | "both" | "neither",
  "activity": "sleeping" | "eating" | "drinking" | "playing" | "grooming" | "sitting" | "walking" | "running" | "jumping" | "climbing" | "cuddling" | "looking" | "exploring" | "resting" | "stretching" | "play_bow" | "belly_up" | "loaf_pose" | "kneading" | "stalking" | "watching" | "being_held" | "being_petted" | "being_groomed" | "hiding" | "eating_grass" | "scratching",
  "activity_notes": "free-text 1 sentence: refine the activity tag if 'other' or if more specific (e.g., 'eating cat grass from bowl', 'being held by hand on chair', 'play-bow position inviting other pet to play', 'staying at door looking at flies').",
  "micro_behaviors": ["play_bow", "발라당 (belly_up_roll)", "식빵자세 (loaf_pose)", "head_butt", "tail_swish", "slow_blink", "ear_perk", "paw_lift", "stalking_crouch", "nose_nudge"],
  "pet_intent": "rest" | "play" | "alert" | "hunt" | "seek_attention" | "explore" | "groom_self" | "groom_other" | "social_invite (play_bow)" | "submit" | "unclear",
  "looking_at": "other_pet" | "human" | "human_hand" | "window" | "door" | "ceiling" | "floor_target" | "insect" | "toy" | "food" | "water" | "outside" | "camera" | "away_from_camera" | "nothing_specific",
  "has_human": false,
  "human_details": "none" | "hand_only" | "hand_holding_pet" | "hand_offering_object" | "partial_body" | "full_person",
  "composition": "closeup" | "medium" | "wide" | "overhead" | "profile" | "back" | "low_angle",
  "lighting": "natural_bright" | "natural_dim" | "indoor_warm" | "indoor_cool" | "backlit" | "flash",
  "mood": "peaceful" | "playful" | "curious" | "sleepy" | "affectionate" | "alert" | "mischievous" | "calm" | "excited",
  "location_specific": "apartment_entrance" | "rooftop_door_area" | "hallway" | "kitchen_table" | "kitchen_counter" | "kitchen_floor" | "living_room_couch" | "living_room_floor" | "bedroom_bed" | "bedroom_floor" | "balcony" | "bathroom" | "cafe" | "outdoor_walk" | "vet" | "car" | "scratcher_corner" | "window_perch" | "other",
  "background_detail": "1-2 sentences specifying the visible objects: what kind of floor (tile/wood/etc), what walls (color, art on walls?), what furniture/props are in view. Include if a door/window/specific architectural element is present and which direction it leads.",
  "contextual_props": ["food_bowl", "water_bowl", "cat_grass", "harness", "leash", "scratcher", "toy_ball", "blanket", "cushion", "carrier", "litter_box", "treats", "human_food", "delivery_box"],
  "quality_score": 0.0-1.0,
  "quality_issues": "none" | "blurry" | "dark" | "overexposed" | "cropped_subject" | "obstructed",
  "decoration_level": "none" | "light" | "heavy",
  "decoration_notes": "any existing stickers, filters, or overlays already on the image",
  "best_for": ["cartoon_sticker", "ai_vtuber", "real_footage"],
  "best_for_reasoning": "why this image suits certain styles",
  "suggested_caption_ko": "이 장면에 어울리는 한국어 캡션 한 줄 — 추측형 어미 권장",
  "suggested_motion_prompt": "if animated: English micro-motion prompt for Veo i2v",
  "uncertainties": ["list any fact you are GUESSING about, as 'field: what is unclear + your best guess'. e.g. 'location: 집 주방인지 카페인지 불확실, 카페 추정'. Empty list [] if everything is clearly visible. ALWAYS add an entry here when you are not sure of the location, who a partial person is, or an ambiguous pose."]
}

Rules:
- **DO NOT EMBELLISH.** Writer treats this as truth. "Leo lying on wooden floor in sunlight" is fine. "Leo peacefully resting in golden afternoon light, savoring a moment of quiet" is bad — strip the narrator color.
- **DO NOT INVENT.** If you don't see a glass table, don't say there's one. If the cat is being held, say so explicitly — don't describe it as if free-standing.
- **PET-SPECIFIC BEHAVIORS MATTER.** A play-bow (front-down/butt-up posture) is NOT "sniffing the floor". A belly-up roll is NOT "sleeping on back". A loaf pose is NOT "sitting normally". Use the activity / micro_behaviors enums precisely.
- **LOOK BEYOND THE PET.** location_specific should say where this is in the apartment — not just "kitchen" but "kitchen_table" or "kitchen_counter". If a door is visible, identify if it's "rooftop_door_area" / "apartment_entrance" / "bathroom_door".
- **SUBJECT INTENT.** What does this pet seem to be DOING / WANTING in this moment? play-bow → "social_invite". Stalking crouch → "hunt". Slow blink at human → "seek_attention" or "rest".
- contextual_props: list ONLY props visibly in frame. If only food bowl visible, that's the only entry. If cat grass is visible, include "cat_grass".
- **LOCATION — DO NOT GUESS A HOME ROOM (PD 2026-06-06).** The home is ONE specific apartment (light wood floors, white walls, blue-cushioned wooden bench sofa, fish tank). If the space does NOT match it — unfamiliar counter/tables, café décor, wooden animal figures/signage, other pets, leashes on chairs, outdoor seating — it is most likely a **cafe** or **other**, NOT "kitchen". Never label a café "주방/kitchen". When the room is genuinely unclear, use location_specific="other" and SAY "장소 불확실" in scene_description rather than guessing.
- **BODY POSE — stretched vs curled are OPPOSITE (PD 2026-06-06).** Look at the legs/body. Legs extended, body long = "몸을 길게 뻗음 (stretched out)" → activity "stretching"/"resting", NOT "웅크림". Body balled up, legs tucked = "웅크림 (curled up)". Do not default a sleeping pet to "웅크림" — describe what you actually see.
- has_human: true if ANY part of a human is visible — **including a partial human in the BACKGROUND** (a skirt, a leg, an arm, a hand at the frame edge, someone seated on the bench behind the pets). When present, say WHERE in scene_description (e.g. "왼쪽 뒤편에 사람의 치마/다리"). Missing a background human causes the Writer to render them as an unexplained surprise.
- quality_score: 1.0 = perfect, 0.5 = usable, 0.0 = unusable.
- Return ONLY valid JSON, no markdown fences, no commentary.
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _call_gemini_vision(image_path: Path) -> dict:
    """Send image to Gemini Flash vision and return parsed JSON.

    PD 2026-06-03 migration: switched from `google.generativeai` (deprecated,
    SDK had broken async DNS resolver causing hour-long hangs in our
    environment) to `google.genai` (new SDK). Synchronous Client uses
    requests-based transport, no async-resolver weirdness.
    """
    from google import genai as _genai
    from google.genai import types as _types
    from PIL import Image

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    # Enable HEIC support (iPhone photos)
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    img = Image.open(image_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    max_dim = 1024
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)

    client = _genai.Client(api_key=api_key)
    model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")
    response = client.models.generate_content(
        model=model_name,
        contents=[
            _types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
            ANALYSIS_PROMPT,
        ],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    text = (response.text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _extract_video_frame(video_path: Path, at_sec: float = 1.0) -> Path | None:
    """Extract a single frame from a video for VLM analysis."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"{video_path.stem}_frame.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(at_sec), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, check=True, timeout=30,
        )
        return out if out.exists() else None
    except Exception:
        return None


def analyze_asset(asset: dict, dry_run: bool = False) -> dict | None:
    """Analyze a single asset and return the VLM result."""
    fp = Path(asset["file_path"])
    if not fp.is_absolute():
        fp = ROOT / fp
    if not fp.exists():
        log.warning("File not found: %s", fp)
        return None

    # For videos, extract a frame
    if asset["kind"] == "video":
        frame = _extract_video_frame(fp)
        if not frame:
            log.warning("Could not extract frame from %s", fp)
            return None
        analyze_path = frame
    else:
        analyze_path = fp

    if dry_run:
        print(f"  [dry-run] Would analyze: {fp.name}")
        return None

    try:
        result = _call_gemini_vision(analyze_path)
        return result
    except Exception as e:
        log.warning("VLM failed for %s: %s", asset["asset_id"], str(e)[:200])
        return None


def _str(val) -> str | None:
    """Coerce any value to string for DB. Lists become comma-joined."""
    if val is None:
        return None
    if isinstance(val, list):
        return ",".join(str(v) for v in val)
    return str(val)


def update_asset_tags(con: sqlite3.Connection, asset_id: str, tags: dict) -> None:
    """Write VLM analysis results to the DB."""
    con.execute(
        """
        UPDATE assets SET
            scene_description = ?,
            activity = ?,
            has_human = ?,
            composition = ?,
            lighting = ?,
            mood = ?,
            background = ?,
            location_tag = ?,
            quality_score = ?,
            focus_subject = ?,
            decoration_level = ?,
            best_for = ?,
            vlm_analyzed_at = datetime('now'),
            notes = ?
        WHERE asset_id = ?
        """,
        (
            _str(tags.get("scene_description")),
            _str(tags.get("activity")),
            1 if tags.get("has_human") else 0,
            _str(tags.get("composition")),
            _str(tags.get("lighting")),
            _str(tags.get("mood")),
            _str(tags.get("background")),
            _str(tags.get("background")),  # also fill location_tag
            tags.get("quality_score"),
            tags.get("focus_subject"),
            tags.get("decoration_level"),
            ",".join(tags.get("best_for", [])) if isinstance(tags.get("best_for"), list) else tags.get("best_for"),
            json.dumps({
                "suggested_caption_ko": tags.get("suggested_caption_ko"),
                "suggested_motion_prompt": tags.get("suggested_motion_prompt"),
                "background_detail": tags.get("background_detail"),
                "quality_issues": tags.get("quality_issues"),
                "decoration_notes": tags.get("decoration_notes"),
                "best_for_reasoning": tags.get("best_for_reasoning"),
                "human_details": tags.get("human_details"),
                # PD 2026-06-02: new richer VLM fields (animal-behavior literacy
                # + spatial context + intent). Producer reads these in addition
                # to scene_description so Writer can ground actions in actual
                # observed micro-behavior + pet intent rather than guessing.
                "activity_notes": tags.get("activity_notes"),
                "micro_behaviors": tags.get("micro_behaviors"),
                "pet_intent": tags.get("pet_intent"),
                "looking_at": tags.get("looking_at"),
                "location_specific": tags.get("location_specific"),
                "contextual_props": tags.get("contextual_props"),
                # PD 2026-06-06: VLM self-flags facts it is GUESSING about
                # (esp. location). A queue surfaces these to PD, whose answer
                # becomes authoritative pd_notes. Stops the Writer from
                # transcribing wrong/uncertain tags as truth.
                "uncertainties": tags.get("uncertainties") or [],
            }, ensure_ascii=False),
            asset_id,
        ),
    )


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="VLM bulk asset tagger")
    p.add_argument("--limit", type=int, default=0, help="max assets to process (0=all)")
    p.add_argument("--force", action="store_true", help="re-analyze already tagged")
    p.add_argument("--asset", default=None, help="single asset_id to analyze")
    p.add_argument("--dry-run", action="store_true", help="test without API calls")
    p.add_argument("--batch-size", type=int, default=20, help="commit every N assets")
    # PD 2026-06-03: re-analyze only assets that haven't been touched since
    # this ISO timestamp (resume a partial --force run without redoing
    # already-done work). Combine with --force semantics implicitly.
    p.add_argument("--since", default=None,
                   help="only analyze assets where vlm_analyzed_at < this ISO "
                        "timestamp (or NULL). e.g. '2026-06-02 13:00:00'")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between API calls")
    args = p.parse_args()

    con = _db()

    if args.asset:
        row = con.execute("SELECT * FROM assets WHERE asset_id LIKE ? || '%'", (args.asset,)).fetchone()
        if not row:
            print(f"Asset not found: {args.asset}", file=sys.stderr)
            return 2
        result = analyze_asset(dict(row), dry_run=args.dry_run)
        if result:
            update_asset_tags(con, row["asset_id"], result)
            con.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Batch mode
    if args.since:
        where = f"(vlm_analyzed_at < '{args.since}' OR vlm_analyzed_at IS NULL)"
    elif args.force:
        where = "1=1"
    else:
        where = "vlm_analyzed_at IS NULL"
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    rows = con.execute(
        f"SELECT * FROM assets WHERE {where} ORDER BY captured_iso DESC {limit_clause}"
    ).fetchall()

    total = len(rows)
    print(f"==> {total} assets to analyze")
    if total == 0:
        print("Nothing to do.")
        return 0

    success = 0
    errors = 0
    for i, row in enumerate(rows, 1):
        asset = dict(row)
        print(f"[{i}/{total}] {asset['asset_id'][:40]} ({asset['kind']})...", end=" ", flush=True)

        result = analyze_asset(asset, dry_run=args.dry_run)
        if result:
            update_asset_tags(con, asset["asset_id"], result)
            success += 1
            activity = result.get("activity", "?")
            human = "👤" if result.get("has_human") else ""
            score = result.get("quality_score", 0)
            print(f"✓ {activity} {human} q={score:.1f}")
        else:
            errors += 1
            print("✗" if not args.dry_run else "(dry)")

        # Periodic commit
        if i % args.batch_size == 0:
            con.commit()
            print(f"  --- committed {i}/{total} ---")

        # Rate limiting
        if not args.dry_run and args.delay > 0:
            time.sleep(args.delay)

    con.commit()
    print(f"\n=== Done: {success} tagged, {errors} errors, {total} total ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
