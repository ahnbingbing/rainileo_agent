"""
agents/cameraman.py — Cameraman Agent (Phase 1).

Renders an approved concept card into a YouTube-ready mp4 by routing to
one of three pipelines:

  ai_vtuber        photo → preprocess → Gemini regen → Veo i2v → captions → assemble
  cartoon_sticker  photo → decorate_photo (PIL stickers) → Veo i2v → captions → assemble
  real_footage     video clips → extract+caption (ffmpeg) → assemble

Run:
    python -m agents.cameraman <card_id_prefix>
    python -m agents.cameraman <card_id_prefix> --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.cameraman")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

# ── BGM mood → candidate tracks (PD 2026-06-07: rf used a flat single-track map
# → every episode sounded identical; route it through this hash-varied picker
# like the ai_vtuber path). Mirrors the in-function map; single source of truth. ──
_BGM_MOOD_MAP: dict[str, list[str]] = {
    "gentle_acoustic": ["cocosmusic-rainbow-lullaby-300370.mp3", "cocosmusic-funshine-melody-281885.mp3", "diana_production_music-little-moments-of-joy-287749.mp3", "sonican-sweet-moments-232318.mp3", "sonican-sweet-moments-optimistic-folk-232685.mp3"],
    "warm_acoustic": ["hunzalaawanarts75-cinematic-cozy-vibes-421335.mp3", "lp-studio-music-background-acoustic-guitar-music-spring-vibes-308989.mp3", "stockaudios-upbeat-acoustic-113986.mp3"],
    "soft_lofi": ["redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3", "9jackjack8-fading-summer-ibiza-chill-house-499753.mp3", "9jackjack8-shallow-water-hawaiian-chill-515350.mp3", "tyler-havlicek-moonless-night-remasterd-by-laura-parrell-416321.mp3"],
    "gentle_ambient": ["cocosmusic-sunny-meadow-song-287479.mp3", "manandnature2024-sunset-escape-soundora-rfm-386107.mp3", "natureseye-always-chanting-intro-outro-10373.mp3", "kuzu420-ambient-electronic-flute-bgm-431329.mp3"],
    "cozy": ["hunzalaawanarts75-cinematic-cozy-vibes-421335.mp3", "redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3", "redproductions-background-delicate-presentation-peaceful-happy-friendly-music-20859.mp3"],
    "lullaby": ["cocosmusic-rainbow-lullaby-300370.mp3", "diana_production_music-little-moments-of-joy-287749.mp3"],
    "playful_upbeat": ["cocosmusic-funshine-groove-281923.mp3", "diana_production_music-bouncing-with-joy-315940.mp3", "diana_production_music-joyride-melody-315944.mp3", "lp-studio-music-fun-and-happy-318796.mp3", "top-flow-happy-day-148320.mp3", "pproducer-happy-positive-rock-151330.mp3", "musictown-upbeat-and-sweet-114196.mp3"],
    "playful_synth": ["cocosmusic-giggly-grooves-290115.mp3", "colorfulsound-go-bounce-158716.mp3", "colorfulsound-letx27s-create-158711.mp3"],
    "upbeat_cute": ["backgroundmusicforvideos-cute-cheerful-whistle-cute-music-249653.mp3", "gensanmaier-cheerful-instrumental-childrenx27s-song-311964.mp3", "redproductions-game-comedy-interesting-playful-sweet-bright-childish-music-57040.mp3", "diana_production_music-bouncy-bunny-trail-335181.mp3"],
    "fun": ["cocosmusic-joyful-jumps-290119.mp3", "lp-studio-music-fun-and-happy-318796.mp3", "lp-studio-music-happy-fun-music-weekend-323148.mp3", "free_audio_library-jolly-jingles-290437.mp3"],
    "whistle": ["audiodollar-ukulele-whistling-451780.mp3", "silentecho-whistle-dance-336938.mp3", "top-flow-whistle-bliss-pop-159649.mp3", "redproductions-pleasure-whistling-hope-joy-playful-bright-claps-music-16522.mp3", "sonican-ukulele-whistle-60-seconds-248093.mp3", "sonican-ukulele-whistle-laid-back-hope-275633.mp3", "dpstudiomusic-background-happy-royalty-free-wanderlust-whistle-307635.mp3"],
    "ukulele": ["audiodollar-ukulele-whistling-451780.mp3", "kaazoom-golden-dayz-upbeat-ukulele-334188.mp3", "kaazoom-happy-and-free-ukulele-with-whistling-and-keyboard-335220.mp3", "kaazoom-hawaiian-shuffle-full-version-happy-ukulele-music-490377.mp3", "kaazoom-you-and-me-carefree-happy-upbeat-ukulele-and-whistling-335212.mp3", "sunnyvibesaudio-laugh-together-happy-upbeat-ukulele-197624.mp3"],
    "chill_documentary": ["9jackjack8-fading-summer-ibiza-chill-house-499753.mp3", "manandnature2024-sunset-escape-soundora-rfm-386107.mp3", "lp-studio-music-summer-bossa-318802.mp3"],
    "chill": ["9jackjack8-shallow-water-hawaiian-chill-515350.mp3", "kuzu420-beginning-is-the-end-chilling-futuristic-ambient-music-233264.mp3", "manandnature2024-sunset-escape-soundora-rfm-386107.mp3"],
    "happy": ["bodleasons-happy-dreams-296347.mp3", "lp-studio-music-happy-music-310817.mp3", "lp-studio-music-happy-positive-background-music-310816.mp3", "paulyudin-happy-sunshine-198392.mp3", "dpstudiomusic-background-happy-royalty-free-joyful-journey-307644.mp3", "dpstudiomusic-background-happy-royalty-free-sunny-strings-307646.mp3", "dpstudiomusic-background-happy-royalty-free-laughing-skies-307648.mp3", "dpstudiomusic-background-happy-royalty-free-harmonic-trails-307650.mp3", "musictown-cheerful-joy-and-celebration-108492.mp3", "surprising_media-drunk-with-happiness-406944.mp3"],
    "bright": ["aliceurbandruid-happy-whistle-travel-445333.mp3", "lp-studio-music-happy-fun-positive-day-313390.mp3", "lp-studio-music-smile-318797.mp3", "lp-studio-music-positive-day-301821.mp3", "lp-studio-music-joyful-journey-301836.mp3"],
    "bossa_summer": ["lp-studio-music-summer-bossa-318802.mp3", "kaazoom-youx27re-my-august-heart-happy-summer-love-song-489902.mp3"],
    "jazz_quirky": ["music_for_videos-fun-amp-quirky-jazz-123607.mp3", "lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3"],
    "country_folk": ["sonican-old-country-joy-201384.mp3", "sonican-old-country-joy-loop-201614.mp3", "dpstudiomusic-background-happy-royalty-free-harvest-hoedown-307638.mp3", "dpstudiomusic-background-happy-royalty-free-banjo-bliss-307640.mp3", "geoffharvey-gone-fishinx27-379984.mp3"],
    "piano_sparkle": ["chrisdjyogi-holiday-sparkle-piano-strings-amp-glockenspiel-421425.mp3"],
    "drama_orchestral": ["sonican-dramatic-orchestral-hope-238050.mp3"],
    "comedy": ["alanajordan-goblin-mode-403685.mp3", "alisiabeats-eat-me-168922.mp3", "lp-studio-music-background-happy-music-funny-friends-308987.mp3", "lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3", "music_for_videos-fun-amp-quirky-jazz-123607.mp3"],
    "bouncy": ["diana_production_music-bouncy-bunny-trail-335181.mp3", "diana_production_music-bouncing-with-joy-315940.mp3", "colorfulsound-go-bounce-158716.mp3", "geoffharvey-playdate-427890.mp3", "dimmysad-rock-your-body-390089.mp3"],
    "picnic": ["cocosmusic-picnic-jam-334903.mp3", "kaazoom-ice-cold-beer-full-version-happy-ukulele-guitar-and-whistling-489893.mp3"],
    "optimistic": ["sonican-optimistic-517106.mp3", "sonican-optimistic-music-hopeful-loop-2-520368.mp3", "diana_production_music-pure-bliss-vibes-315943.mp3", "lemonmusicstudio-completely-satisfied-135249.mp3", "kmacleod-pickled-pink-127675.mp3"],
    "pet_themed": ["lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3", "diana_production_music-bouncy-bunny-trail-335181.mp3", "grumpynora-piggy-loop-1-409462.mp3", "grumpynora-piggy-loop-2-409463.mp3"],
    "spooky": ["melodyayresgriffiths-the-spirit-in-the-castle-dungeon-spooky-soundtrack-halloween-420820.mp3", "surprising_media-whistle-in-the-dark-439161.mp3"],
}


def _pick_bgm_track(bgm_mood: str, seed_key: str) -> str:
    """Deterministic-but-varied BGM filename for a mood: hash(seed_key) indexes
    into the mood's candidates so repeated renders of one episode are stable but
    different episodes vary even within a mood. Falls back to a cute default."""
    import hashlib as _h
    candidates = _BGM_MOOD_MAP.get(
        bgm_mood, ["backgroundmusicforvideos-cute-cheerful-whistle-cute-music-249653.mp3"])
    seed = int(_h.sha1((seed_key or "default").encode("utf-8")).hexdigest()[:8], 16)
    return candidates[seed % len(candidates)]
BUMPER_MUSIC = ROOT / os.getenv(
    "BUMPER_MUSIC",
    "assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3",
)
DEFAULT_BGM = ROOT / os.getenv(
    "MAIN_BGM",
    "assets/bgm/sonican-optimistic-music-hopeful-loop-2-520368.mp3",
)
INTRO_BUMPER = ROOT / "assets" / "branding" / "intro_bumper.mp4"
OUTRO_BUMPER = ROOT / "assets" / "branding" / "outro_bumper.mp4"

# Real BytePlus model IDs (the legacy "seedance-2.0" string was not a valid ID).
# 2026-05-31: default → fast model (2x faster, ~$0.30 cheaper per cut).
# PD accepted that detailed prompts compensate for fast model quality.
# Trade-off: ref mode has 5s duration cap on fast — Cameraman clamps.
# Env override SEEDANCE_MODEL=dreamina-seedance-2-0-260128 to use standard.
DEFAULT_MODEL_SEEDANCE = "dreamina-seedance-2-0-fast-260128"
# Hard cap for fast model in ref mode (BytePlus limitation)
FAST_MODEL_REF_MAX_SECONDS = 5

ProgressCb = Callable[[str], None] | None


# ──────────────────────────────────────────────────────────────────────
# Seedance reference image library
# ──────────────────────────────────────────────────────────────────────
# Logical name → path under ROOT. Director outputs logical names; this maps
# them to actual files. Missing files fall back to "pair".
REF_LIBRARY = {
    "pair":         "assets/character_ref/official_ryani_leo.png",
    "pair_cafe":    "assets/character_ref/official_ryani_leo_cafe.png",
    "photo_real":   "assets/character_ref/photo_ref_both.png",
    # Per-character / per-pose slots — these files may not exist yet.
    # Director can name them; _resolve_ref() falls back to "pair" cleanly.
    "ryani_solo":       "assets/character_ref/ryani_solo.png",
    "leo_solo":         "assets/character_ref/leo_solo.png",
    "ryani_playbow":    "assets/character_ref/ryani_playbow.png",
    "leo_pounce":       "assets/character_ref/leo_pounce.png",
    "leo_question_tail":"assets/character_ref/leo_question_tail.png",
}


def _resolve_ref(name: str) -> Path | None:
    """Resolve a Director-supplied logical ref name to an actual file path,
    or fall back to the default `pair` ref. Returns None only if even the
    default ref is missing."""
    rel = REF_LIBRARY.get(name)
    if rel:
        p = ROOT / rel
        if p.exists():
            return p
        log.warning("ref %s -> %s missing, falling back to 'pair'", name, p)
    pair = ROOT / REF_LIBRARY["pair"]
    return pair if pair.exists() else None


def _resolve_scene_ref(set_anchor: str | None, set_description: str,
                       fallback_out_path: Path,
                       dry_run: bool = False) -> Path | None:
    """Find or build the scene reference image for this concept's set.

    Resolution order:
      1. **Canonical library** — `data/set_library.json[set_anchor].scene_ref`
         points to `assets/scene_refs/<set_anchor>.png` (built once via
         `scripts/build_scene_refs.py`, shared across ALL episodes that use
         this set). This is the default for any known set.
      2. **Per-render fallback** — if the set is special/unknown (Director's
         set_description doesn't match any library set_anchor), generate a
         one-shot empty-set image into `fallback_out_path` (work_dir/scene_ref.png).
         Cost ~$0.10 once per render.

    Returns the resolved scene ref path or None if neither is available.
    """
    # 1. Canonical library lookup
    if set_anchor:
        try:
            lib = json.loads((ROOT / "data" / "set_library.json").read_text(encoding="utf-8"))
            entry = lib.get(set_anchor)
            if entry and entry.get("scene_ref"):
                lib_path = ROOT / entry["scene_ref"]
                if lib_path.exists() and lib_path.stat().st_size > 10_000:
                    log.info("scene_ref from library: %s → %s", set_anchor, lib_path.name)
                    return lib_path
                else:
                    log.warning("scene_ref %s referenced but file missing: %s",
                                set_anchor, lib_path)
        except Exception as e:
            log.warning("set_library lookup failed: %s", e)

    # 2. Per-render fallback
    if fallback_out_path.exists() and fallback_out_path.stat().st_size > 10_000:
        return fallback_out_path
    if dry_run:
        log.info("[dry-run] would generate fallback scene_ref at %s", fallback_out_path)
        return None
    if not set_description or len(set_description) < 30:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai package not available, skipping scene ref")
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY not set, skipping scene ref")
        return None

    prompt = (
        f"Empty room photograph, no animals, no pets, no people, no living "
        f"creatures. Just the empty space as described. "
        f"Photographic, realistic, sharp focus on the room.\n\n"
        f"{set_description}"
    )
    try:
        import base64
        client = OpenAI()
        result = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            quality="high",
            n=1,
        )
        png_bytes = base64.b64decode(result.data[0].b64_json)
        fallback_out_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_out_path.write_bytes(png_bytes)
        log.info("scene_ref fallback generated (%d KB) → %s",
                 len(png_bytes) // 1024, fallback_out_path.name)
        return fallback_out_path
    except Exception as e:
        log.warning("scene_ref fallback generation failed: %s", e)
        return None


# Back-compat shim — earlier code path called _generate_scene_ref directly.
def _generate_scene_ref(set_description: str, out_path: Path,
                        dry_run: bool = False) -> Path | None:
    return _resolve_scene_ref(
        set_anchor=None,
        set_description=set_description,
        fallback_out_path=out_path,
        dry_run=dry_run,
    )


def _extract_frame(video_path: Path, time_sec: float, out_jpg: Path) -> None:
    """Extract a single frame from a video at the given time, save as JPEG."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{time_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2",
        str(out_jpg),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not out_jpg.exists():
        raise RuntimeError(
            f"frame extract failed (rc={proc.returncode}) for {video_path} @ {time_sec}s: "
            f"{proc.stderr[-400:]}"
        )


# ──────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _load_card(con: sqlite3.Connection, prefix: str) -> dict:
    row = con.execute(
        "SELECT * FROM cards WHERE card_id LIKE ? || '%' AND state IN ('approved', 'rendered') LIMIT 1",
        (prefix,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"No approved card matching '{prefix}'")
    return dict(row)


def _load_card_assets(con: sqlite3.Connection, card_id: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT ca.*, a.file_path, a.kind, a.subjects_csv, a.captured_iso
        FROM card_assets ca
        JOIN assets a ON ca.asset_id = a.asset_id
        WHERE ca.card_id = ?
        ORDER BY ca.role, ca.asset_id
        """,
        (card_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _log_run_start(con: sqlite3.Connection, card_id: str) -> int:
    cur = con.execute(
        "INSERT INTO runs (agent, card_id, status) VALUES ('cameraman', ?, 'running')",
        (card_id,),
    )
    con.commit()
    return cur.lastrowid


def _log_run_end(con: sqlite3.Connection, run_id: int, status: str,
                 output: str | None = None, error: str | None = None) -> None:
    con.execute(
        "UPDATE runs SET finished_at=datetime('now'), status=?, output_snapshot=?, error_message=? WHERE id=?",
        (status, output, error, run_id),
    )
    con.commit()


# ──────────────────────────────────────────────────────────────────────
# Style routing
# ──────────────────────────────────────────────────────────────────────
VALID_STYLES = ("ai_vtuber", "real_footage")


def determine_render_style(card: dict, assets: list[dict],
                           concept: dict | None = None) -> str:
    explicit = card.get("render_style")

    # Override check: if all resolved assets are video, force real_footage
    if assets and all(a.get("kind") == "video" for a in assets):
        if explicit == "ai_vtuber":
            log.info("Style override: ai_vtuber → real_footage (all assets are video)")
        return "real_footage"

    # If all resolved assets are photos, force ai_vtuber
    if assets and all(a.get("kind") == "photo" for a in assets):
        if explicit == "real_footage":
            log.info("Style override: real_footage → ai_vtuber (all assets are photos)")
            return "ai_vtuber"

    if explicit and explicit in VALID_STYLES:
        return explicit

    payload = json.loads(card.get("payload_json", "{}"))

    # hero_motion present → ai_vtuber
    if payload.get("hero_motion"):
        return "ai_vtuber"

    # Memory Lane imagined_together → ai_vtuber
    lane = payload.get("memory_lane") or {}
    if lane.get("variant") == "imagined_together":
        return "ai_vtuber"

    # AI augmentation explicitly set → ai_vtuber
    aug = payload.get("ai_augmentation") or {}
    if aug.get("needed") and aug.get("type") in ("i2v_compose", "imagined_youth_illustration"):
        return "ai_vtuber"

    # Default for photos → ai_vtuber
    return "ai_vtuber"


# ──────────────────────────────────────────────────────────────────────
# Manifest generation
# ──────────────────────────────────────────────────────────────────────
def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return s.strip("_")[:20] or "cut"


def _wrap_caption_standalone(text: str, font_size: int = 48, max_width: int = 900) -> str:
    """Pixel-based caption wrapping (standalone version for t2v manifests)."""
    if not text:
        return text
    if "\n" in text:
        return "\n".join(_wrap_caption_standalone(line, font_size, max_width)
                         for line in text.split("\n"))
    try:
        from PIL import ImageFont
        font = None
        for fp in [
            Path.home() / "Library" / "Fonts" / "NanumPenScript-Regular.ttf",
            Path.home() / "Library" / "Fonts" / "Pretendard-Bold.otf",
        ]:
            if fp.exists():
                font = ImageFont.truetype(str(fp), font_size)
                break
        if not font:
            font = ImageFont.load_default()
        if font.getlength(text) <= max_width:
            return text
        words = text.split()
        lines, current = [], ""
        for word in words:
            test = f"{current} {word}".strip() if current else word
            if font.getlength(test) > max_width and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)
        return "\n".join(lines)
    except Exception:
        words = text.split()
        lines, current = [], ""
        for word in words:
            if current and len(current) + 1 + len(word) > 20:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip() if current else word
        if current:
            lines.append(current)
        return "\n".join(lines)


def _clean_caption_standalone(text: str) -> str:
    """Clean caption text (standalone version for t2v manifests).

    Only filters out text that looks like script instructions (full-line patterns),
    NOT single English words that might appear in legitimate captions.
    """
    if not text:
        return ""
    # Full-line instruction patterns (these indicate the whole text is a script note)
    instruction_patterns = [
        "컷 ", "보조 컷", "장면 —", "조건 충족", "강조.", "구분됨.",
        "배경 패턴", "촬영본으로",
        "Cut description:", "Scene description:", "regen_prompt",
        "motion_prompt", "veo_prompt", "asset_id:", "camera_move:",
    ]
    if re.match(r'^[A-Z]\.\s', text):
        text = re.sub(r'^[A-Z]\.\s*', '', text)
    # Only filter if the WHOLE text looks like an instruction (not partial match)
    text_stripped = text.strip()
    if any(text_stripped.startswith(sig) for sig in instruction_patterns):
        return ""
    if any(sig in text for sig in ["regen_prompt", "motion_prompt", "veo_prompt", "asset_id:"]):
        return ""
    # Remove parentheses
    text = re.sub(r'^\((.+)\)$', r'\1', text.strip())
    text = text.replace('(', '').replace(')', '')
    import unicodedata
    cleaned = ""
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(("L", "N", "P", "Z")) or ch in "!?.,…~—–·''""[]{}":
            cleaned += ch
    return cleaned.strip()


def _generate_t2v_manifests(card: dict, concept: dict, concept_cuts: list[dict],
                            style: str, work_dir: Path) -> dict:
    """Generate manifests for text-to-video mode (no source images needed).

    Each cut has a `veo_prompt` that Veo uses to generate video from text.
    No regen_prompts or sources needed.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"generation_mode": "text_to_video"}

    # ── Build cut tags (skip cuts with empty veo_prompt) ──
    cuts = []
    seen_slugs: dict[str, int] = {}
    for i, cut in enumerate(concept_cuts):
        prompt = cut.get("veo_prompt", "").strip()
        if not prompt:
            log.warning("Scene %d has empty veo_prompt — skipping", i + 1)
            continue
        beat = cut.get("beat", f"scene{i+1}")
        slug = _slugify(beat)
        # Deduplicate tags (e.g. multiple "closer" beats)
        seen_slugs[slug] = seen_slugs.get(slug, 0) + 1
        if seen_slugs[slug] > 1:
            slug = f"{slug}_{seen_slugs[slug]}"
        tag = f"scene{len(cuts)+1}_{slug}"
        cuts.append({
            "tag": tag,
            "veo_prompt": prompt,
            "duration_seconds": cut.get("duration_seconds", 4),
            "asset": {},  # no asset for t2v
        })
    if not cuts:
        raise RuntimeError("No scenes with veo_prompt — cannot generate text-to-video")
    result["cuts"] = cuts

    # ── Veo prompts manifest ──
    veo_prompts = {}
    for item in cuts:
        veo_prompts[item["tag"]] = {
            "prompt": item["veo_prompt"],
            "seconds": item["duration_seconds"],
        }
    veo_path = work_dir / "veo_prompts.json"
    veo_path.write_text(json.dumps(veo_prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    result["veo_prompts"] = str(veo_path)

    # ── Captions manifest ──
    fallback_title = _clean_caption_standalone((concept or {}).get("title", ""))
    captions = {}
    for i, item in enumerate(cuts):
        scenes = []
        if i < len(concept_cuts):
            cut_data = concept_cuts[i]
            if "captions" in cut_data and isinstance(cut_data["captions"], list):
                for sc in cut_data["captions"]:
                    raw_ko = sc.get("ko", "") or ""
                    raw_en = sc.get("en", "") or ""
                    # Defensive split: if Writer/Polisher merged KO+EN into ko
                    # field with `\n` separator and left en empty, split here.
                    # Detect by: `\n` in ko + en empty + the post-`\n` segment
                    # is ASCII-dominant (English).
                    if "\n" in raw_ko and not raw_en.strip():
                        head, _, tail = raw_ko.partition("\n")
                        ascii_ratio = (
                            sum(1 for c in tail if ord(c) < 128) / max(len(tail), 1)
                        )
                        if ascii_ratio > 0.7:  # tail looks English
                            raw_ko = head
                            # Also strip any further `\n` from tail (forced wrap)
                            raw_en = tail.replace("\n", " ").strip()
                    ko_clean = _clean_caption_standalone(raw_ko)
                    en_clean = _clean_caption_standalone(raw_en)
                    if ko_clean or en_clean:
                        scene = {
                            "start": sc.get("start", 0.2),
                            "end": sc.get("end", 4.0),
                            "ko": ko_clean,
                            "en": en_clean,
                        }
                        if sc.get("position") in ("top", "bottom"):
                            scene["position"] = sc["position"]
                        scenes.append(scene)
        if not scenes:
            scenes = [{"start": 0.2, "end": 4.0, "ko": fallback_title or "...", "en": ""}]
        cut_caption_pos = cut_data.get("caption_position") if (i < len(concept_cuts)) else None
        entry = {"scenes": scenes}
        if cut_caption_pos in ("top", "bottom"):
            entry["caption_position"] = cut_caption_pos
        captions[item["tag"]] = entry

    # Per-cut tempo (Director's `tempo_factor`) → embedded in captions manifest
    # under `_tempo_factors`. assemble_episode.py reads this and applies
    # setpts per cut (살랑살랑 = 0.85 / default = 1.3 / fast = 1.6).
    tempo_factors: dict[str, float] = {}
    for i, item in enumerate(cuts):
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        tf = cc.get("tempo_factor")
        if tf is None:
            continue
        try:
            sp = float(tf)
            if 0.5 <= sp <= 2.0:
                tempo_factors[item["tag"]] = sp
        except (TypeError, ValueError):
            continue
    if tempo_factors:
        captions["_tempo_factors"] = tempo_factors

    captions_path = work_dir / "captions.json"
    captions_path.write_text(json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8")
    result["captions"] = str(captions_path)

    # ── BGM ──
    # 93 tracks total; mood → list of candidates. Pick deterministically
    # by card_id hash so repeated renders of the same episode get the same
    # track, but different episodes naturally vary even within the same
    # mood. PD 2026-06-01: previous single-track-per-mood made every
    # playful episode sound identical.
    bgm_mood = concept.get("bgm_mood", "")
    bgm_map: dict[str, list[str]] = {
        # quiet / gentle
        "gentle_acoustic": [
            "cocosmusic-rainbow-lullaby-300370.mp3",
            "cocosmusic-funshine-melody-281885.mp3",
            "diana_production_music-little-moments-of-joy-287749.mp3",
            "sonican-sweet-moments-232318.mp3",
            "sonican-sweet-moments-optimistic-folk-232685.mp3",
        ],
        "warm_acoustic": [
            "hunzalaawanarts75-cinematic-cozy-vibes-421335.mp3",
            "lp-studio-music-background-acoustic-guitar-music-spring-vibes-308989.mp3",
            "stockaudios-upbeat-acoustic-113986.mp3",
        ],
        "soft_lofi": [
            "redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3",
            "9jackjack8-fading-summer-ibiza-chill-house-499753.mp3",
            "9jackjack8-shallow-water-hawaiian-chill-515350.mp3",
            "tyler-havlicek-moonless-night-remasterd-by-laura-parrell-416321.mp3",
        ],
        "gentle_ambient": [
            "cocosmusic-sunny-meadow-song-287479.mp3",
            "manandnature2024-sunset-escape-soundora-rfm-386107.mp3",
            "natureseye-always-chanting-intro-outro-10373.mp3",
            "kuzu420-ambient-electronic-flute-bgm-431329.mp3",
        ],
        "cozy": [
            "hunzalaawanarts75-cinematic-cozy-vibes-421335.mp3",
            "redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3",
            "redproductions-background-delicate-presentation-peaceful-happy-friendly-music-20859.mp3",
        ],
        "lullaby": [
            "cocosmusic-rainbow-lullaby-300370.mp3",
            "diana_production_music-little-moments-of-joy-287749.mp3",
        ],
        # playful / fun
        "playful_upbeat": [
            "cocosmusic-funshine-groove-281923.mp3",
            "diana_production_music-bouncing-with-joy-315940.mp3",
            "diana_production_music-joyride-melody-315944.mp3",
            "lp-studio-music-fun-and-happy-318796.mp3",
            "top-flow-happy-day-148320.mp3",
            "pproducer-happy-positive-rock-151330.mp3",
            "musictown-upbeat-and-sweet-114196.mp3",
        ],
        "playful_synth": [
            "cocosmusic-giggly-grooves-290115.mp3",
            "colorfulsound-go-bounce-158716.mp3",
            "colorfulsound-letx27s-create-158711.mp3",
        ],
        "upbeat_cute": [
            "backgroundmusicforvideos-cute-cheerful-whistle-cute-music-249653.mp3",
            "gensanmaier-cheerful-instrumental-childrenx27s-song-311964.mp3",
            "redproductions-game-comedy-interesting-playful-sweet-bright-childish-music-57040.mp3",
            "diana_production_music-bouncy-bunny-trail-335181.mp3",
        ],
        "fun": [
            "cocosmusic-joyful-jumps-290119.mp3",
            "lp-studio-music-fun-and-happy-318796.mp3",
            "lp-studio-music-happy-fun-music-weekend-323148.mp3",
            "free_audio_library-jolly-jingles-290437.mp3",
        ],
        "whistle": [
            "audiodollar-ukulele-whistling-451780.mp3",
            "silentecho-whistle-dance-336938.mp3",
            "top-flow-whistle-bliss-pop-159649.mp3",
            "redproductions-pleasure-whistling-hope-joy-playful-bright-claps-music-16522.mp3",
            "sonican-ukulele-whistle-60-seconds-248093.mp3",
            "sonican-ukulele-whistle-laid-back-hope-275633.mp3",
            "dpstudiomusic-background-happy-royalty-free-wanderlust-whistle-307635.mp3",
        ],
        "ukulele": [
            "audiodollar-ukulele-whistling-451780.mp3",
            "kaazoom-golden-dayz-upbeat-ukulele-334188.mp3",
            "kaazoom-happy-and-free-ukulele-with-whistling-and-keyboard-335220.mp3",
            "kaazoom-hawaiian-shuffle-full-version-happy-ukulele-music-490377.mp3",
            "kaazoom-you-and-me-carefree-happy-upbeat-ukulele-and-whistling-335212.mp3",
            "sunnyvibesaudio-laugh-together-happy-upbeat-ukulele-197624.mp3",
        ],
        # mood
        "chill_documentary": [
            "9jackjack8-fading-summer-ibiza-chill-house-499753.mp3",
            "manandnature2024-sunset-escape-soundora-rfm-386107.mp3",
            "lp-studio-music-summer-bossa-318802.mp3",
        ],
        "chill": [
            "9jackjack8-shallow-water-hawaiian-chill-515350.mp3",
            "kuzu420-beginning-is-the-end-chilling-futuristic-ambient-music-233264.mp3",
            "manandnature2024-sunset-escape-soundora-rfm-386107.mp3",
        ],
        "happy": [
            "bodleasons-happy-dreams-296347.mp3",
            "lp-studio-music-happy-music-310817.mp3",
            "lp-studio-music-happy-positive-background-music-310816.mp3",
            "paulyudin-happy-sunshine-198392.mp3",
            "dpstudiomusic-background-happy-royalty-free-joyful-journey-307644.mp3",
            "dpstudiomusic-background-happy-royalty-free-sunny-strings-307646.mp3",
            "dpstudiomusic-background-happy-royalty-free-laughing-skies-307648.mp3",
            "dpstudiomusic-background-happy-royalty-free-harmonic-trails-307650.mp3",
            "musictown-cheerful-joy-and-celebration-108492.mp3",
            "surprising_media-drunk-with-happiness-406944.mp3",
        ],
        "bright": [
            "aliceurbandruid-happy-whistle-travel-445333.mp3",
            "lp-studio-music-happy-fun-positive-day-313390.mp3",
            "lp-studio-music-smile-318797.mp3",
            "lp-studio-music-positive-day-301821.mp3",
            "lp-studio-music-joyful-journey-301836.mp3",
        ],
        # NEW moods (PD 2026-06-01)
        "bossa_summer": [
            "lp-studio-music-summer-bossa-318802.mp3",
            "kaazoom-youx27re-my-august-heart-happy-summer-love-song-489902.mp3",
        ],
        "jazz_quirky": [
            "music_for_videos-fun-amp-quirky-jazz-123607.mp3",
            "lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3",
        ],
        "country_folk": [
            "sonican-old-country-joy-201384.mp3",
            "sonican-old-country-joy-loop-201614.mp3",
            "dpstudiomusic-background-happy-royalty-free-harvest-hoedown-307638.mp3",
            "dpstudiomusic-background-happy-royalty-free-banjo-bliss-307640.mp3",
            "geoffharvey-gone-fishinx27-379984.mp3",
        ],
        "piano_sparkle": [
            "chrisdjyogi-holiday-sparkle-piano-strings-amp-glockenspiel-421425.mp3",
        ],
        "drama_orchestral": [
            "sonican-dramatic-orchestral-hope-238050.mp3",
        ],
        "comedy": [
            "alanajordan-goblin-mode-403685.mp3",
            "alisiabeats-eat-me-168922.mp3",
            "lp-studio-music-background-happy-music-funny-friends-308987.mp3",
            "lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3",
            "music_for_videos-fun-amp-quirky-jazz-123607.mp3",
        ],
        "bouncy": [
            "diana_production_music-bouncy-bunny-trail-335181.mp3",
            "diana_production_music-bouncing-with-joy-315940.mp3",
            "colorfulsound-go-bounce-158716.mp3",
            "geoffharvey-playdate-427890.mp3",
            "dimmysad-rock-your-body-390089.mp3",
        ],
        "picnic": [
            "cocosmusic-picnic-jam-334903.mp3",
            "kaazoom-ice-cold-beer-full-version-happy-ukulele-guitar-and-whistling-489893.mp3",
        ],
        "optimistic": [
            "sonican-optimistic-517106.mp3",
            "sonican-optimistic-music-hopeful-loop-2-520368.mp3",
            "diana_production_music-pure-bliss-vibes-315943.mp3",
            "lemonmusicstudio-completely-satisfied-135249.mp3",
            "kmacleod-pickled-pink-127675.mp3",
        ],
        "pet_themed": [
            "lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3",
            "diana_production_music-bouncy-bunny-trail-335181.mp3",
            "grumpynora-piggy-loop-1-409462.mp3",
            "grumpynora-piggy-loop-2-409463.mp3",
        ],
        "spooky": [
            "melodyayresgriffiths-the-spirit-in-the-castle-dungeon-spooky-soundtrack-halloween-420820.mp3",
            "surprising_media-whistle-in-the-dark-439161.mp3",
        ],
    }
    candidates = bgm_map.get(
        bgm_mood,
        ["backgroundmusicforvideos-cute-cheerful-whistle-cute-music-249653.mp3"],
    )
    # Deterministic varied pick: hash(card_id) → index into candidates
    import hashlib as _h
    card_id = card.get("card_id", "") or concept.get("title", "") or "default"
    seed = int(_h.sha1(card_id.encode("utf-8")).hexdigest()[:8], 16)
    bgm_file = candidates[seed % len(candidates)]
    result["bgm"] = str(ROOT / "assets" / "bgm" / bgm_file)
    result["bgm_mood_used"] = bgm_mood
    result["bgm_pick_count"] = len(candidates)

    return result


def generate_manifests(card: dict, assets: list[dict], style: str,
                       work_dir: Path, concept: dict | None = None) -> dict:
    """Generate pipeline manifests from card + concept storyboard.

    If `concept` is provided (from Producer), its cut descriptions drive
    captions and asset selection. Otherwise falls back to card payload.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(card.get("payload_json", "{}"))
    draft = payload.get("draft", {})

    # ── Get storyboard cuts from concept (preferred) or card ──
    # PD 2026-06-02: when re-rendering an existing card without re-running
    # Writer, `concept` is None. Previously this caused Cameraman to skip the
    # card's stored asset_ids and fall back to a DB-wide subjects search →
    # captions stayed but clips changed to random recent footage. Multiple
    # re-renders of the same card produced different-content videos with the
    # same broken captions. Now: fall back to card.payload cuts when concept
    # is not provided.
    concept_cuts = (concept or {}).get("cuts", []) or payload.get("cuts", [])
    generation_mode = (concept or {}).get("generation_mode",
                                          payload.get("generation_mode", "image_to_video"))

    # ── text_to_video mode: no assets needed, build from veo_prompts ──
    if generation_mode == "text_to_video" and concept_cuts:
        return _generate_t2v_manifests(card, concept, concept_cuts, style, work_dir)

    # ── image_to_video mode (original): find assets ──
    ordered = []

    # Priority 1: concept has specific asset_ids per cut
    # Filter by expected kind for the style
    expected_kind = "video" if style == "real_footage" else "photo"

    if concept_cuts and any(c.get("asset_id") or c.get("seedance_mode") == "interp"
                            for c in concept_cuts):
        con = _db()
        # PD 2026-06-04: keep cut↔asset 1:1 aligned. Previously a skipped
        # asset (wrong kind / missing) did `continue` WITHOUT dropping the
        # cut, shifting every later cut's asset by one → captions landed on
        # the wrong clips. Now: build (cut, asset) pairs in lockstep; drop
        # invalid cuts entirely so caption + clip stay paired.
        valid_cuts = []
        for cut in concept_cuts:
            aid = cut.get("asset_id")
            mode = cut.get("seedance_mode", "")
            if aid:
                row = con.execute(
                    "SELECT asset_id, file_path, kind, subjects_csv, captured_iso, "
                    "duration_sec, width, height, has_human, source_uuid, "
                    "NULL as role, NULL as trim_start, NULL as trim_end "
                    "FROM assets WHERE asset_id = ?", (aid,)
                ).fetchone()
                if not row:
                    log.warning("Dropping cut — asset_id %s not found", str(aid)[:24])
                    continue
                asset = dict(row)
                # PD 2026-06-06: for real_footage, a PHOTO is NOT an invalid
                # asset — keep the cut and let the sources builder route it to
                # Seedance photo_i2v (Tier 2) so it gets motion. Previously
                # photos were dropped here, silently truncating the story
                # (e.g. the warm payoff finale vanished → abrupt ending).
                if asset["kind"] != expected_kind:
                    if style == "real_footage" and asset["kind"] == "photo":
                        log.info("real_footage: photo cut %s → Seedance photo_i2v",
                                 str(aid)[:20])
                    else:
                        log.warning("Dropping cut — %s kind=%s but style=%s needs %s",
                                    str(aid)[:20], asset["kind"], style, expected_kind)
                        continue
                ordered.append(asset)
                valid_cuts.append(cut)
            elif mode == "interp" and style == "real_footage":
                ordered.append({
                    "asset_id": None,
                    "kind": "interp_fill",
                    "file_path": None,
                    "subjects_csv": "",
                    "duration_sec": cut.get("duration_seconds", 4),
                    "width": None, "height": None,
                    "role": None, "trim_start": None, "trim_end": None,
                })
                valid_cuts.append(cut)
        # Re-bind concept_cuts to the surviving aligned set so downstream
        # caption/manifest building uses the same indices as `ordered`.
        if valid_cuts:
            concept_cuts = valid_cuts

    # Priority 2: card_assets linked
    if not ordered:
        primary = [a for a in assets if a["role"] == "primary"]
        supporting = [a for a in assets if a["role"] == "supporting"]
        ordered = primary + supporting
        if not ordered:
            ordered = assets[:5]

    # Priority 3: fallback DB search
    if not ordered:
        con = _db()
        kind_filter = "video" if style == "real_footage" else "photo"
        subjects = (concept or {}).get("subjects", ["ryani", "leo"])
        sub_clauses = " OR ".join(["subjects_csv LIKE ?"] * len(subjects))
        sub_params = [f"%{s}%" for s in subjects]
        rows = con.execute(
            f"SELECT asset_id, file_path, kind, subjects_csv, captured_iso, "
            f"duration_sec, width, height, NULL as role, NULL as trim_start, NULL as trim_end "
            f"FROM assets WHERE kind=? AND ({sub_clauses}) "
            f"AND file_path NOT LIKE '%.heic' "
            f"ORDER BY quality_score DESC, captured_iso DESC LIMIT ?",
            [kind_filter] + sub_params + [max(len(concept_cuts), 4)],
        ).fetchall()
        ordered = [dict(r) for r in rows]
        if not ordered:
            raise RuntimeError(f"No {kind_filter} assets found in DB for subjects={subjects}")

    # ── Build cut tags ──
    theme_slug = _slugify(card.get("theme") or "cut")
    n_cuts = len(concept_cuts) if concept_cuts else min(len(ordered), 4)
    cuts = []
    for i in range(n_cuts):
        if i < len(concept_cuts):
            cc = concept_cuts[i]
            beat = cc.get("beat") or cc.get("tag") or f"cut{i+1}"
        else:
            beat = f"cut{i+1}"
        tag = f"cut{i+1}_{_slugify(beat)}"
        asset = ordered[i] if i < len(ordered) else ordered[-1]
        cuts.append({"tag": tag, "asset": asset})

    # ── Sources manifest ──
    sources = {}
    if style == "real_footage":
        # PD 2026-06-02: real_footage v2 supports 3 source tiers per cut.
        # Tier 1 (clip) = direct video trim. Tier 2 (photo_i2v) = animate
        # a real photo via Seedance i2v. Tier 3 (chain_from_prev) = i2v
        # from previous cut's last frame.
        for i, item in enumerate(cuts):
            tag = item["tag"]
            a = item["asset"]
            # Pull source_hint from the matching concept_cut (Director sets it)
            cc = concept_cuts[i] if i < len(concept_cuts) else {}
            hint = (cc.get("source_hint") or "").strip().lower()

            if hint == "photo_i2v" or a.get("kind") == "photo":
                # Tier 2: photo as i2v input. Director's motion_prompt drives the
                # 5s animation.
                photo_fp = a.get("file_path") or cc.get("photo_path", "")
                if photo_fp and not Path(photo_fp).is_absolute():
                    photo_fp = str(ROOT / photo_fp)
                sources[tag] = {
                    "source": "__photo_i2v__",
                    "photo_path": photo_fp,
                    "source_uuid": a.get("source_uuid") or "",  # for on-demand re-download
                    "motion_prompt": cc.get("motion_prompt", ""),
                    "seedance_seconds": int(cc.get("duration_seconds") or 5),
                    "trim_start": 0.0,
                    "trim_dur": float(cc.get("duration_seconds") or 5),
                }
                continue
            if hint == "chain_from_prev" or a.get("kind") == "chain_fill":
                # Tier 3: chain from previous cut's last frame.
                sources[tag] = {
                    "source": "__chain_from_prev__",
                    "motion_prompt": cc.get("motion_prompt", ""),
                    "seedance_seconds": int(cc.get("duration_seconds") or 5),
                    "trim_start": 0.0,
                    "trim_dur": float(cc.get("duration_seconds") or 5),
                }
                continue
            if a.get("kind") == "interp_fill":
                # Legacy interp gap-fill (between two real clips).
                sources[tag] = {
                    "source": "__interp_pending__",
                    "trim_start": 0.0,
                    "trim_dur": 4.0,
                    "interp": True,
                }
                continue
            # Tier 1 (default): real video clip, direct ffmpeg trim.
            fp = a.get("file_path", "")
            if fp and not Path(fp).is_absolute():
                fp = str(ROOT / fp)
            # PD 2026-06-02 fix: writer's duration_seconds was being ignored
            # because the asset_id query forces trim_end=NULL, falling back to
            # a hardcoded 4.0s ceiling. Episode bodies came out ~16s for
            # 4×6s storyboards. Now: trim_dur = writer's duration_seconds,
            # capped by source clip's actual duration_sec when known.
            ts = float(a.get("trim_start") or 0.0)
            writer_dur = float(cc.get("duration_seconds") or 0.0)
            te = a.get("trim_end")
            if te is not None:
                td = float(te) - ts
            elif writer_dur > 0:
                td = writer_dur
            else:
                td = 4.0
            src_dur = a.get("duration_sec")
            if src_dur:
                td = min(td, max(0.5, float(src_dur) - ts))
            src_entry = {
                "source": fp,
                "trim_start": ts,
                "trim_dur": td,
                # PD 2026-06-06: carry has_human so the trim step can crop the
                # human out — channel HARD RULE: a human FACE must NEVER be
                # visible. Also carry duration_sec for last-cut 여운 (play real
                # footage instead of a frozen still).
                "has_human": int(a.get("has_human") or 0),
                "src_dur": float(src_dur) if src_dur else None,
                # PD 2026-06-07 efficient model: uuid lets the trim step
                # re-download the original on demand if it was pruned.
                "source_uuid": a.get("source_uuid") or "",
            }
            # PD 2026-06-03: split_screen modes need a SECOND asset for the
            # other half of the split. Writer sets cc.secondary_asset_id.
            edit_effect = (cc.get("edit_effect") or "").strip().lower()
            sec_aid = cc.get("secondary_asset_id")
            if edit_effect in ("split_horizontal", "split_vertical") and sec_aid:
                sec_con = _db()
                sec_row = sec_con.execute(
                    "SELECT file_path, duration_sec FROM assets WHERE asset_id = ?",
                    (sec_aid,),
                ).fetchone()
                if sec_row:
                    sec_fp = sec_row["file_path"]
                    if sec_fp and not Path(sec_fp).is_absolute():
                        sec_fp = str(ROOT / sec_fp)
                    src_entry["secondary_source"] = sec_fp
                    src_entry["secondary_trim_start"] = 0.0
                    src_entry["edit_effect"] = edit_effect
                    src_entry["__needs_split_prerender__"] = True
                else:
                    log.warning("split_screen: secondary_asset_id %s not "
                                "found, falling back to static", sec_aid)
            sources[tag] = src_entry
    else:
        for item in cuts:
            a = item["asset"]
            fp = a["file_path"]
            if not Path(fp).is_absolute():
                fp = str(ROOT / fp)
            # PD 2026-06-08: the efficient-storage model prunes originals after VLM
            # tagging, but the av i2v preprocess NEEDS the source photo. Re-download
            # it on demand by source_uuid (this was why av produced 0 episodes —
            # every cut's photo was "not found"). uuid from the asset or the DB.
            if not Path(fp).exists():
                uuid = a.get("source_uuid")
                if not uuid and a.get("asset_id"):
                    try:
                        _r = _db().execute(
                            "SELECT source_uuid FROM assets WHERE asset_id=?",
                            (a["asset_id"],)).fetchone()
                        uuid = _r[0] if _r else None
                    except Exception:
                        uuid = None
                rec = _ensure_local(fp, uuid)
                if rec:
                    fp = rec
            sources[item["tag"]] = fp

    sources_path = work_dir / "sources.json"
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Captions manifest — use concept captions (priority) or descriptions ──
    def _wrap_caption(text: str, font_size: int = 48, max_width: int = 900) -> str:
        """Auto-wrap captions based on pixel width. Dynamic font sizing.

        1. Try current font_size — if fits, done
        2. If not, word-wrap to multiple lines
        3. Always ensures no line exceeds max_width
        """
        if not text:
            return text
        # Respect existing newlines but also check each line
        if "\n" in text:
            lines = text.split("\n")
            wrapped = []
            for line in lines:
                wrapped.append(_wrap_caption(line, font_size, max_width))
            return "\n".join(wrapped)

        try:
            from PIL import ImageFont
            font = None
            for fp in [
                Path.home() / "Library" / "Fonts" / "Pretendard-Bold.otf",
                Path.home() / "Library" / "Fonts" / "NanumPenScript-Regular.ttf",
            ]:
                if fp.exists():
                    font = ImageFont.truetype(str(fp), font_size)
                    break
            if not font:
                font = ImageFont.load_default()

            # Check if it fits in one line
            if font.getlength(text) <= max_width:
                return text

            # Word-wrap by pixel width
            words = text.split()
            lines = []
            current = ""
            for word in words:
                test = f"{current} {word}".strip() if current else word
                if font.getlength(test) > max_width and current:
                    lines.append(current)
                    current = word
                else:
                    current = test
            if current:
                lines.append(current)
            return "\n".join(lines)
        except Exception:
            # Fallback: wrap at ~20 chars
            words = text.split()
            lines, current = [], ""
            for word in words:
                if current and len(current) + 1 + len(word) > 20:
                    lines.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip() if current else word
            if current:
                lines.append(current)
            return "\n".join(lines)

    # Clean captions helper
    def _clean_caption(text: str) -> str:
        if not text:
            return ""
        # Full-line instruction patterns (NOT partial English words)
        instruction_patterns = [
            "컷 ", "보조 컷", "장면 —", "조건 충족", "강조.", "구분됨.",
            "배경 패턴", "촬영본으로",
            "Cut description:", "Scene description:",
        ]
        # Always-filter keywords (technical terms that never appear in captions)
        always_filter = ["regen_prompt", "motion_prompt", "veo_prompt", "asset_id:"]
        import re as _re
        if _re.match(r'^[A-Z]\.\s', text):
            text = _re.sub(r'^[A-Z]\.\s*', '', text)
        text_stripped = text.strip()
        if any(text_stripped.startswith(sig) for sig in instruction_patterns):
            return ""
        if any(sig in text for sig in always_filter):
            return ""
        # Remove parentheses wrapping — "(텍스트)" → "텍스트"
        import re as _re2
        text = _re2.sub(r'^\((.+)\)$', r'\1', text.strip())
        text = text.replace('(', '').replace(')', '')
        import unicodedata
        cleaned = ""
        for ch in text:
            cat = unicodedata.category(ch)
            if cat.startswith(("L", "N", "P", "Z")) or ch in "!?.,…~—–·''""[]{}":
                cleaned += ch
        return cleaned.strip()

    fallback_title = _clean_caption((concept or {}).get("title", ""))

    captions = {}
    for i, item in enumerate(cuts):
        scenes = []

        if i < len(concept_cuts):
            cut_data = concept_cuts[i]

            def _merge_ko_en(ko_text: str, en_text: str) -> str:
                """Merge KO + EN into one text block: KO line above, EN line below.
                Both are pixel-wrapped independently to prevent overflow."""
                ko_clean = _clean_caption(ko_text)
                en_clean = _clean_caption(en_text)
                # Dynamic: burn_captions will auto-size font to fit 960px usable width.
                # Here we just ensure no single line is absurdly long.
                # Use 960px (1080-120 padding) as max, font 40px as reference.
                max_w = 960
                if ko_clean and en_clean:
                    ko_wrapped = _wrap_caption(ko_clean, font_size=40, max_width=max_w)
                    en_wrapped = _wrap_caption(en_clean, font_size=40, max_width=max_w)
                    return f"{ko_wrapped}\n{en_wrapped}"
                elif ko_clean:
                    return _wrap_caption(ko_clean, font_size=40, max_width=max_w)
                elif en_clean:
                    return _wrap_caption(en_clean, font_size=40, max_width=max_w)
                return ""

            # PD 2026-06-06: real_footage keeps KO/EN as SEPARATE fields so
            # burn_captions.build_vf_multi can apply the intended hierarchy —
            # KO big in the handwriting font, EN smaller in Pretendard below it.
            # _merge_ko_en (used by ai_vtuber) stuffed both into `ko` with a
            # newline + cleared `en`, so English rendered in the Korean
            # handwriting font at KO size and the KO block ran "한 줄 더" long
            # (PD repeat issue #4). Only real_footage gets the split for now.
            split_ko_en = (style == "real_footage")

            # Priority 1: concept has "captions" array (multi-scene)
            if "captions" in cut_data and isinstance(cut_data["captions"], list):
                for sc in cut_data["captions"]:
                    if split_ko_en:
                        ko_clean = _clean_caption(sc.get("ko", ""))
                        en_clean = _clean_caption(sc.get("en", ""))
                        if ko_clean or en_clean:
                            scenes.append({
                                "start": sc.get("start", 0.2),
                                "end": sc.get("end", 4.0),
                                "ko": ko_clean, "en": en_clean,
                            })
                    else:
                        merged = _merge_ko_en(sc.get("ko", ""), sc.get("en", ""))
                        if merged:
                            scenes.append({
                                "start": sc.get("start", 0.2),
                                "end": sc.get("end", 4.0),
                                "ko": merged, "en": "",
                            })

            # Priority 2: single caption_ko + caption_en
            if not scenes:
                if split_ko_en:
                    ko_clean = _clean_caption(
                        cut_data.get("caption_ko", cut_data.get("description", "")))
                    en_clean = _clean_caption(cut_data.get("caption_en", ""))
                    if ko_clean or en_clean:
                        scenes.append({"start": 0.2, "end": 4.0,
                                       "ko": ko_clean, "en": en_clean})
                else:
                    merged = _merge_ko_en(
                        cut_data.get("caption_ko", cut_data.get("description", "")),
                        cut_data.get("caption_en", ""),
                    )
                    if merged:
                        scenes.append({"start": 0.2, "end": 4.0, "ko": merged, "en": ""})

        # Priority 3: fallback to title
        if not scenes and fallback_title:
            scenes.append({"start": 0.2, "end": 4.0, "ko": fallback_title, "en": ""})

        # PD 2026-06-06: captions MUST be continuous — a frame with NO caption is
        # a SERIOUS defect (PD: "자막 영상 불일치는 심각한 이슈"). Make scenes
        # gap-free and cover the whole cut: the first appears at ~0.1s, each
        # scene's end = the next scene's start (fills any gap), and the last
        # extends to the cut's end. Applies to BOTH lanes now — ai_vtuber
        # one-take cuts were leaving the first ~2s blank. EXCEPTION: a
        # wink_ending cut keeps its intentional late/brief caption.
        _cc_here = concept_cuts[i] if i < len(concept_cuts) else {}
        _is_wink = _cc_here.get("function") == "wink_ending"
        if scenes and not _is_wink:
            # PD 2026-06-08: distribute captions across the ACTUAL clip duration
            # (sources[tag].trim_dur), not the concept's requested duration. Short
            # source clips were keeping captions timed to 7.5s → last-scene caption
            # overflowed a 4s clip ("캡션 많은데 영상 짧고"). Also drop captions that
            # can't get a readable min display, and spread the rest evenly (gap-free,
            # no racing).
            MIN_SCENE = float(os.getenv("CAPTION_MIN_SEC", "1.8"))
            try:
                _concept_dur = float(_cc_here.get("duration_seconds") or 0)
            except Exception:
                _concept_dur = 0.0
            try:
                _actual = float(sources.get(item["tag"], {}).get("trim_dur") or 0)
            except Exception:
                _actual = 0.0
            span = _actual or _concept_dur or (len(scenes) * MIN_SCENE)
            scenes.sort(key=lambda s: float(s.get("start", 0)))
            # cap caption count to what fits the clip at a readable pace
            max_n = max(1, int(span / MIN_SCENE))
            if len(scenes) > max_n:
                log.info("cut %s: dropping %d caption(s) to fit %.1fs clip",
                         item["tag"], len(scenes) - max_n, span)
                scenes = scenes[:max_n]
            # even, gap-free distribution across the actual clip span
            n = len(scenes)
            for j, s in enumerate(scenes):
                s["start"] = round(0.1 if j == 0 else j * span / n, 2)
                s["end"] = round((j + 1) * span / n if j < n - 1 else max(span - 0.05, s["start"] + 1.0), 2)

        # Always use scenes format (works for both real_footage and ai_vtuber)
        captions[item["tag"]] = {"scenes": scenes} if scenes else {
            "scenes": [{"start": 0.2, "end": 4.0, "ko": fallback_title or "...", "en": ""}]
        }

    captions_path = work_dir / "captions.json"
    captions_path.write_text(json.dumps(captions, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "sources": str(sources_path),
        "captions": str(captions_path),
        "cuts": cuts,
        "concept": concept or {},  # full concept dict (set_description, regen_direction, etc.)
        "concept_cuts": concept_cuts,  # per-cut Director metadata (seedance_mode, references, fill_anchors)
    }

    # ── BGM selection based on concept mood (PD 2026-06-07: hash-varied pick so
    # rf episodes don't all sound the same — same picker as the ai_vtuber path). ──
    bgm_mood = (concept or {}).get("bgm_mood", "")
    _seed = (card.get("card_id") if isinstance(card, dict) else "") \
        or (concept or {}).get("title", "") or "default"
    bgm_file = _pick_bgm_track(bgm_mood, _seed)
    result["bgm"] = str(ROOT / "assets" / "bgm" / bgm_file)

    # ── Regen prompts (ai_vtuber / cartoon_sticker — concept-driven) ──
    if style in ("ai_vtuber", "cartoon_sticker"):  # cartoon_sticker legacy → same as ai_vtuber
        preserve = (
            "The pet's breed, fur color, markings, AND SEX must be preserved "
            "exactly. "
            "Ryani is FEMALE (she/her, 11yo senior female French Bulldog — "
            "channel's 랴니엄마). Smooth feminine underbelly, NO male "
            "genitalia of any kind. Petite/refined feminine build, NOT "
            "muscular male. THIN Boston Terrier-style white blaze (a NARROW "
            "line, NOT a thick wide splash) from nose to forehead, white "
            "dot above each eye, silver-grey aged muzzle, white chin, "
            "white chest patch. Only black/white/grey — no brown. "
            "ABSOLUTELY NO TAIL (French Bulldog — her rear is bare and "
            "tailless; never render any tail). "
            "Leo is MALE (he/him, 8mo young male orange tabby — channel's "
            "아들 레오). "
            "Behavior trait: when food drops to the floor, RYANI is the "
            "crumb specialist who picks it up — Leo does not eat off the "
            "floor."
        )

        # Get regen direction from concept (priority) or generate generic
        regen_dir = (concept or {}).get("regen_direction", {})
        overall_style = regen_dir.get("overall_style", "")
        if not overall_style:
            # Fallback: generate from card tone/theme
            theme = card.get("theme", "")
            tone = card.get("tone_primary", "warm")
            overall_style = f"Cute pet illustration, {tone} mood, {theme} theme"

        regen = {
            "_base_style": overall_style,
            "_preserve_subjects": preserve,
            "_color_palette": regen_dir.get("color_palette", ""),
            "_texture": regen_dir.get("texture", ""),
            "_mood_atmosphere": regen_dir.get("mood_atmosphere", ""),
        }

        for i, item in enumerate(cuts):
            tag = item["tag"]
            cc = concept_cuts[i] if i < len(concept_cuts) else {}
            mode = cc.get("seedance_mode", "i2v")
            # Skip GPT image-gen for ref/interp cuts — Seedance handles these
            # directly without a pre-generated still.
            if mode in ("ref", "interp"):
                continue
            subjects = item.get("asset", {}).get("subjects_csv", "pet")
            per_cut_prompt = cc.get("regen_prompt", "")
            full_prompt = f"{overall_style}. {per_cut_prompt}. " \
                          f"Featuring {subjects}. {preserve}"
            regen[tag] = full_prompt

        regen_path = work_dir / "regen_prompts.json"
        regen_path.write_text(json.dumps(regen, ensure_ascii=False, indent=2), encoding="utf-8")
        result["regen_prompts"] = str(regen_path)

        # Store motion prompts from concept cuts
        result["motion_prompts"] = {}
        for i, item in enumerate(cuts):
            if i < len(concept_cuts):
                mp = concept_cuts[i].get("motion_prompt", "")
                if mp:
                    result["motion_prompts"][item["tag"]] = mp

    return result


# ──────────────────────────────────────────────────────────────────────
# Subprocess runner
# ──────────────────────────────────────────────────────────────────────
def _vlm_post_render_caption_rewrite(work_dir: Path, manifests: dict,
                                        cuts: list[dict], concept_cuts: list[dict],
                                        anim_dir: Path, progress_cb=None,
                                        dry_run: bool = False) -> None:
    """PD 2026-06-02: VLM ground-truth check + Caption Agent re-write.

    For each non-wink cut in the animated dir:
    1. Extract 3 keyframes (0.5s/2.5s/4.5s).
    2. Send to Gemini 2.5 Flash with the cut's metadata for a 1-2 sentence
       Korean description of what's REALLY happening.
    3. Augment concept_cuts with `vlm_actual_action`.
    4. Re-run Caption Agent (3-way + judge) using the VLM context.
    5. Update the captions manifest (manifests["captions"] JSON file).

    Silent fallback on any failure. Wink cuts always skip (no caption).
    """
    if dry_run:
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.info("VLM rewrite: no GOOGLE_API_KEY, skipping")
        return
    if progress_cb:
        progress_cb(":mag: [4b/6] VLM 후처리 검수 + 캡션 재작성 시작...")
    # 1-3. VLM describe per cut. PD 2026-06-08: was legacy google.generativeai →
    # 600s hang per call on DNS blips (×3×6 cuts = hours; this stalled av AFTER the
    # Seedance cuts were already billed). NEW google.genai SDK with http timeout.
    _vlm_sys = (
        "You watch a 5-second YouTube Short cut for the 'Ryani & Leo' channel and "
        "describe in 1-2 short Korean sentences what ACTUALLY happens. Be specific "
        "about subject positions, movements, AND any explicit sounds (짖다/왕왕/야옹/"
        "냐옹). If a pet doesn't bark or meow, do NOT mention it. Ground-truth "
        "observer only — no speculation.")
    try:
        from google import genai as _g
        from google.genai import types as _gt
        _vlm_client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        _vlm_model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")
    except Exception as e:
        log.warning("VLM init failed: %s", e); return

    from PIL import Image as PILImage
    n_described = 0
    for i, item in enumerate(cuts):
        tag = item.get("tag")
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        if not tag:
            continue
        if cc.get("function") == "wink_ending":
            cc["vlm_actual_action"] = "[wink — no caption needed]"
            continue
        mp4 = anim_dir / f"{tag}.mp4"
        if not mp4.exists():
            continue
        frames: list[Path] = []
        for t in (0.5, 2.5, 4.5):
            jpg = work_dir / f"_vlm_{tag}_{t}.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(t), "-i", str(mp4),
                     "-frames:v", "1", "-q:v", "3", str(jpg)],
                    capture_output=True, check=False, timeout=10,
                )
                if jpg.exists() and jpg.stat().st_size > 1000:
                    frames.append(jpg)
            except Exception:
                continue
        if not frames:
            continue
        parts = [f"Cut: {tag}. 3 keyframes at 0.5s, 2.5s, 4.5s of this 5s clip."]
        for j in frames:
            try:
                parts.append(_gt.Part.from_bytes(data=j.read_bytes(),
                                                 mime_type="image/jpeg"))
            except Exception:
                pass
        parts.append("1-2 short Korean sentences describing what actually happens.")
        # PD 2026-06-02: VLM 실패 = 재도전. PD 2026-06-08: 2회로 축소 + bounded
        # timeout (DNS blip이 600s×3 행이던 것 해소).
        actual = ""
        for attempt in range(2):
            try:
                resp = _vlm_client.models.generate_content(
                    model=_vlm_model_name, contents=parts,
                    config=_gt.GenerateContentConfig(
                        system_instruction=_vlm_sys,
                        thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
                actual = (resp.text or "").strip()
                if actual:
                    break
            except Exception as e:
                log.warning("VLM describe attempt %d/2 failed for %s: %s",
                            attempt + 1, tag, e)
                actual = ""
        if actual:
            cc["vlm_actual_action"] = actual
            n_described += 1
        else:
            log.warning("VLM describe gave up on %s after 3 attempts", tag)
    if progress_cb:
        progress_cb(f":mag: VLM 분석 {n_described}/{len(cuts)} cuts 완료")
    if n_described == 0:
        log.info("VLM rewrite: no cuts described, skipping caption regen")
        return

    # 4. Re-run Caption Agent — PD 2026-06-02: 실패 = 재도전. 3회 시도.
    new_concept = None
    for attempt in range(3):
        try:
            from agents.writer_director import run_caption_agent
            concept = dict(manifests.get("concept") or {})
            concept["cuts"] = concept_cuts
            updated = run_caption_agent([concept], progress_cb=progress_cb)
            if updated and updated[0].get("cuts"):
                new_concept = updated[0]
                break
        except Exception as e:
            log.warning("Caption Agent re-run attempt %d/3 failed: %s",
                        attempt + 1, e)
            if progress_cb:
                progress_cb(f":repeat: Caption Agent 재시도 ({attempt+1}/3)")
    if not new_concept:
        log.warning("Caption Agent re-run gave up after 3 attempts — keeping originals")
        return

    # 5. Update captions.json manifest
    captions_path = Path(manifests.get("captions") or "")
    if not captions_path.exists():
        log.warning("captions manifest missing for VLM rewrite update")
        return
    try:
        cap_data = json.loads(captions_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("captions.json parse fail: %s", e); return
    n_updated = 0
    # PD 2026-06-06 FIX: the Caption Agent's returned cuts sometimes carry tags
    # that don't match captions.json keys (e.g. "cut1" vs "cut1_one_take") → the
    # rewrite matched 0 cuts and the original (often gappy/mismatched) captions
    # were kept ("VLM 캡션 0컷 재작성"). Fall back to POSITIONAL matching when the
    # tag doesn't resolve, so the re-grounded captions actually apply.
    ordered_tags = [k for k in cap_data.keys() if not k.startswith("_")]
    new_cuts = new_concept.get("cuts") or []
    for idx, new_cut in enumerate(new_cuts):
        tag = new_cut.get("tag") or new_cut.get("cut_tag")
        if not tag or tag not in cap_data:
            tag = ordered_tags[idx] if idx < len(ordered_tags) else None
        if not tag or tag not in cap_data:
            continue
        new_caps = new_cut.get("captions") or []
        if not new_caps:
            continue
        cap_data[tag]["scenes"] = new_caps
        # Drop legacy top-level ko/en
        cap_data[tag].pop("ko", None)
        cap_data[tag].pop("en", None)
        n_updated += 1
    captions_path.write_text(
        json.dumps(cap_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("VLM rewrite: %d cuts captions updated", n_updated)
    if progress_cb:
        progress_cb(f":sparkles: [4b/6] VLM 캡션 {n_updated}컷 재작성 완료")


def _burn_captions_cmd(manifests: dict, in_dir: Path, out_dir: Path) -> list[str]:
    """Build burn_captions.py command with optional font override from Director."""
    cmd = [
        "python3", "scripts/burn_captions.py",
        "--manifest", manifests["captions"],
        "--in-dir", str(in_dir),
        "--out-dir", str(out_dir),
    ]
    # Director/Producer가 특별 컨셉용 폰트 지정 (부처님 오신날, 크리스마스 등)
    font = manifests.get("font_override")
    if font:
        font_path = ROOT / font if not Path(font).is_absolute() else Path(font)
        if font_path.exists():
            cmd.extend(["--font", str(font_path)])
    return cmd


def _run(cmd: list[str], step: str, progress_cb: ProgressCb = None,
         dry_run: bool = False) -> subprocess.CompletedProcess | None:
    if progress_cb:
        progress_cb(step)
    log.info("[%s] %s", step, " ".join(cmd[:6]))
    if dry_run:
        log.info("  [dry-run] %s", " ".join(cmd))
        print(f"  [dry-run] {' '.join(cmd)}")
        return None
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=600)
    if proc.returncode != 0:
        err = proc.stderr[-2000:] if proc.stderr else proc.stdout[-2000:]
        raise RuntimeError(f"Step '{step}' failed (rc={proc.returncode}):\n{err}")
    return proc


# ──────────────────────────────────────────────────────────────────────
# Pipeline: real_footage
# ──────────────────────────────────────────────────────────────────────
def _prerender_interp_fills(manifests: dict, work_dir: Path,
                            progress_cb: ProgressCb = None,
                            dry_run: bool = False) -> None:
    """For each real_footage cut marked as a Seedance interp gap-fill, extract
    anchor frames from the surrounding real clips and call Seedance interp.
    Rewrites the sources manifest in place so the rest of the pipeline treats
    the generated mp4 as a normal clip.

    Constraints (enforced by the Director prompt; we just verify):
      - Interp cuts must NOT be at the edges (need a real neighbor on each side).
      - Each fill_anchors.before_asset_id / after_asset_id must reference a
        cut tag that is itself a real clip in this manifest.
    """
    sources_path = Path(manifests["sources"])
    if not sources_path.exists():
        return
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    interp_tags = [t for t, v in sources.items()
                   if isinstance(v, dict) and v.get("interp")]
    if not interp_tags:
        return

    concept_cuts = manifests.get("concept_cuts", [])
    cuts = manifests.get("cuts", [])
    tag_index = {item["tag"]: i for i, item in enumerate(cuts)}
    tag_list = [item["tag"] for item in cuts]

    use_seedance = bool(os.environ.get("BYTEPLUS_API_KEY", ""))
    if not use_seedance and not dry_run:
        raise RuntimeError(
            f"real_footage has {len(interp_tags)} interp fill cut(s) but "
            "BYTEPLUS_API_KEY is not set — cannot render Seedance interp."
        )

    fill_dir = work_dir / "interp_fills"
    fill_dir.mkdir(parents=True, exist_ok=True)

    for tag in interp_tags:
        idx = tag_index.get(tag)
        if idx is None or idx == 0 or idx == len(tag_list) - 1:
            raise RuntimeError(
                f"interp fill at edge position not allowed: tag={tag} idx={idx}/{len(tag_list)}"
            )
        cc = concept_cuts[idx] if idx < len(concept_cuts) else {}
        anchors = cc.get("fill_anchors") or {}

        # Resolve neighbor cuts. Director may name them either by their
        # asset_id (e.g. "med_2026_...") or by their cut tag. Try cut-tag first.
        before_ref = anchors.get("before_asset_id") or tag_list[idx - 1]
        after_ref = anchors.get("after_asset_id") or tag_list[idx + 1]

        before_tag = before_ref if before_ref in sources else tag_list[idx - 1]
        after_tag = after_ref if after_ref in sources else tag_list[idx + 1]
        before_src = sources.get(before_tag, {})
        after_src = sources.get(after_tag, {})
        before_path = Path(before_src.get("source", ""))
        after_path = Path(after_src.get("source", ""))
        if not before_path.exists() or not after_path.exists():
            raise RuntimeError(
                f"interp anchors missing files: before={before_path} after={after_path}"
            )

        # Frame timestamps: last frame of before, first frame of after.
        before_dur = float(before_src.get("trim_dur", 4.0))
        before_start = float(before_src.get("trim_start", 0.0))
        last_frame_time = before_start + max(before_dur - 0.05, 0.0)
        first_frame_time = float(after_src.get("trim_start", 0.0)) + 0.05

        first_jpg = fill_dir / f"{tag}_first.jpg"
        last_jpg = fill_dir / f"{tag}_last.jpg"
        out_mp4 = fill_dir / f"{tag}.mp4"

        if progress_cb:
            progress_cb(
                f":frame_with_picture: Interp fill {tag}: anchors "
                f"{before_tag}@{last_frame_time:.2f}s → {after_tag}@{first_frame_time:.2f}s"
            )

        if not dry_run:
            _extract_frame(before_path, last_frame_time, first_jpg)
            _extract_frame(after_path, first_frame_time, last_jpg)
        else:
            log.info("[dry-run] would extract %s + %s", first_jpg.name, last_jpg.name)

        prompt = cc.get("motion_prompt") or "smooth natural transition motion"
        seconds = str(int(cc.get("duration_seconds", 4)))
        cmd = [
            "python3", "scripts/animate_seedance_i2v.py",
            "--mode", "interp",
            "--image", str(first_jpg),
            "--last-frame", str(last_jpg),
            "--prompt", prompt,
            "--seconds", seconds,
            "--model", os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE),
            "--output", str(out_mp4),
        ]
        _run(cmd, f":film_frames: [0/3] Seedance interp fill {tag}",
             progress_cb, dry_run)

        # Rewrite sources manifest entry so extract_clips_ep04 treats it
        # as a normal video.
        sources[tag] = {
            "source": str(out_mp4),
            "trim_start": 0.0,
            "trim_dur": float(seconds),
        }

    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _append_character_canon(prompt: str) -> str:
    """PD 2026-06-06: re-state Ryani/Leo canonical appearance so Seedance keeps
    them ON-MODEL. Used by BOTH ai_vtuber and real_footage photo_i2v — without
    it, i2v drifts the pet into a different-looking animal (PD: '랴니가 완전
    다른 캐릭터'). Same canon text as the ai_vtuber per-cut marking injection."""
    pl = (prompt or "").lower()
    if any(k in pl for k in ("ryani", "랴니", "french bulldog", "dog", "강아지", "개")):
        ryani_canon = (
            "Ryani is a SPAYED FEMALE 11-year-old senior French Bulldog "
            "(she/her). Markings CONSISTENT: THIN Boston Terrier-style white "
            "blaze (a narrow line, NOT a wide splash) from nose to forehead, "
            "white dot above each eye, silver-grey aged muzzle, white chin, "
            "large white chest patch, bat ears, ABSOLUTELY NO TAIL, petite "
            "refined feminine body (NOT muscular), only black/white/grey — no "
            "brown. Keep her EXACTLY as in the source photo."
        )
        if "Boston Terrier-style white blaze" not in prompt:
            prompt = prompt + " " + ryani_canon
    if any(k in pl for k in ("leo", "레오", "orange tabby", "cat", "고양이")):
        leo_canon = (
            "Leo is a MALE 8-month-old orange tabby cat (he/him). Pale "
            "yellow-green chartreuse eyes, white chin tuft, lean agile body, "
            "paler cream-orange cheeks and belly than the back. Keep him "
            "EXACTLY as in the source photo."
        )
        if "chartreuse eyes" not in prompt:
            prompt = prompt + " " + leo_canon
    return prompt


def _probe_rotation(path: Path) -> int:
    """Display rotation of a video (degrees, e.g. -90/90/180) from side_data or
    the legacy rotate tag. 0 if none/unknown. Used to bake rotation before crop."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream_side_data=rotation:stream_tags=rotate",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            s = line.strip()
            if s.lstrip("-").isdigit():
                return int(s)
    except Exception:
        pass
    return 0


def _measure_clip_motion(mp4: Path) -> float:
    """Average frame-to-frame luma change — a proxy for how much MOVES in the
    clip. ~1.5 = near-static (still photo with at most a zoom); ~5+ = clearly
    moving subject. Used to guarantee photo_i2v cuts actually animate the pet."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostats", "-loglevel", "error", "-i", str(mp4),
             "-vf", "tblend=all_mode=difference,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YAVG:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, check=False,
        )
        vals = [float(l.split("=")[-1]) for l in proc.stdout.splitlines()
                if "YAVG" in l]
        return sum(vals) / len(vals) if vals else 0.0
    except Exception:
        return 0.0


# Below this average motion, a photo_i2v clip reads as a frozen still — PD
# 2026-06-06 HARD requirement: the pet MUST visibly move on photo/image cuts.
PHOTO_I2V_MIN_MOTION = float(os.getenv("PHOTO_I2V_MIN_MOTION", "3.0"))


def _find_replacement_real_clip(subject: str, exclude: set | None = None) -> dict | None:
    """PD 2026-06-08: when an rf photo_i2v cut of Ryani drifts her markings, swap it
    for a REAL video clip (no Seedance distortion). Find a quality real clip with the
    needed subject, ensure it's local (re-download if pruned). Returns a sources-entry
    dict or None."""
    exclude = exclude or set()
    try:
        con = _db()
        rows = con.execute(
            "SELECT asset_id, file_path, duration_sec, has_human, source_uuid, "
            "subjects_csv FROM assets WHERE kind='video' AND vlm_analyzed_at IS NOT NULL "
            "AND quality_score >= 0.7 AND subjects_csv LIKE '%ryani%' "
            "ORDER BY quality_score DESC, captured_iso DESC LIMIT 30"
        ).fetchall()
    except Exception as e:
        log.warning("replacement clip query failed: %s", e)
        return None
    for r in rows:
        aid = r[0]
        if aid in exclude:
            continue
        if subject == "both" and "leo" not in (r[5] or "").lower():
            continue  # need both pets
        fp = r[1]
        if fp and not Path(fp).is_absolute():
            fp = str(ROOT / fp)
        if not fp or not Path(fp).exists():
            fp = _ensure_local(fp, r[4])  # re-download pruned
        if fp and Path(fp).exists():
            return {"source": fp, "trim_start": 0.0,
                    "trim_dur": min(5.0, float(r[2]) if r[2] else 5.0),
                    "has_human": int(r[3] or 0),
                    "src_dur": float(r[2]) if r[2] else None,
                    "source_uuid": r[4] or "", "asset_id": aid}
    return None


def _prerender_photo_i2v_cuts(manifests: dict, work_dir: Path,
                                progress_cb: ProgressCb = None,
                                dry_run: bool = False) -> None:
    """Real_footage Tier 2 (PD 2026-06-02): cuts marked
    `source = "__photo_i2v__"` get rendered via Seedance i2v from the
    `photo_path`. Replaces the sources entry with the rendered mp4.
    """
    sources_path = Path(manifests["sources"])
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    # PD 2026-06-06: output to a SEPARATE dir (not animated/) so the later
    # _trim_real_footage_clips step reads this mp4 as its input and writes the
    # trimmed result into animated/. If we wrote here straight to animated/,
    # trim's same-path guard would skip the cut, robbing photo cuts of their
    # edit_effect AND the last-cut 여운 freeze extension.
    work_anim = work_dir / "photo_i2v"
    work_anim.mkdir(parents=True, exist_ok=True)
    n_rendered = 0
    for tag, entry in list(sources.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("source") != "__photo_i2v__":
            continue
        photo_path = entry.get("photo_path")
        # PD 2026-06-07 efficient model: re-download the photo on demand if it
        # was pruned (we kept its UUID).
        if (not photo_path or not Path(photo_path).exists()) and entry.get("source_uuid"):
            photo_path = _ensure_local(photo_path, entry.get("source_uuid")) or photo_path
        if not photo_path or not Path(photo_path).exists():
            log.warning("photo_i2v: photo not found for %s", tag)
            continue
        out_mp4 = work_anim / f"{tag}.mp4"
        # PD 2026-06-06: the PET (character) must visibly move, not just a
        # camera zoom — same way ai_vtuber drives Seedance: a character-action
        # prompt with the camera held still. BUT keep it NATURAL, not dramatic:
        # real_footage must not read as generative/regen. Animated photos are a
        # brief mid-roll supplement, so the motion is a small sign of life
        # (blink / ear twitch / slight shift), not a big repositioning.
        # Model matches ai_vtuber (SEEDANCE_MODEL, fast default); override with
        # PHOTO_I2V_MODEL if a cut needs richer motion.
        # PD 2026-06-06: the PET must CLEARLY move — a "subtle breathe + zoom"
        # prompt makes Seedance freeze the pet and only zoom ("정지 화면 줌인줌아웃").
        # Mandate a visible body action; camera held still so the motion reads
        # as the animal, not the camera.
        motion = entry.get("motion_prompt") or (
            "The pet clearly moves: it lifts and turns its head, looks around, "
            "blinks, and shifts its body — visible natural motion. The cat may "
            "sway its tail. Camera stays completely still, no zoom."
        )
        seconds = str(int(entry.get("seedance_seconds") or 5))
        # PD 2026-06-06: default photo_i2v to the STANDARD model — verified to
        # give visible PET motion on a real photo, where the fast model only
        # applied a camera zoom (PD: "i2v 동작 안 함"). Only 0-2 photo cuts per
        # episode, so the extra time is negligible. Override via PHOTO_I2V_MODEL.
        photo_model = os.getenv("PHOTO_I2V_MODEL", "dreamina-seedance-2-0-260128")
        # PD 2026-06-06 HARD requirement: on a photo/image cut the CHARACTER
        # must visibly move — a zoom is NOT motion. We don't just hope the
        # prompt works: render, MEASURE the motion, and re-render with an
        # escalating prompt until the pet clearly moves (or we run out of tries).
        max_tries = int(os.getenv("PHOTO_I2V_MAX_TRIES", "3"))
        escalation = [
            "",
            " Make the head turn and the body shift clearly visible across the "
            "whole clip.",
            " The pet noticeably moves its head, looks around, and repositions "
            "its body — clearly animated, not a still image.",
        ]
        best_motion = -1.0
        for attempt in range(max_tries):
            prompt = motion + (escalation[attempt] if attempt < len(escalation) else escalation[-1])
            # PD 2026-06-06: anchor Ryani/Leo appearance (same canon ai_vtuber
            # uses) so the animated photo doesn't drift into a different-looking
            # pet ("랴니가 완전 다른 캐릭터").
            prompt = _append_character_canon(prompt)
            cmd = [
                "python3", "scripts/animate_seedance_i2v.py",
                "--mode", "i2v",
                "--image", str(photo_path),
                "--prompt", prompt,
                "--seconds", seconds,
                "--model", photo_model,
                "--output", str(out_mp4),
            ]
            _run(cmd,
                 f":frame_with_picture: [0/3] Photo→i2v {tag} (char-motion 시도 {attempt+1})",
                 progress_cb, dry_run)
            if dry_run or not out_mp4.exists():
                break
            mv = _measure_clip_motion(out_mp4)
            log.info("photo_i2v %s attempt %d motion=%.2f (min %.1f)",
                     tag, attempt + 1, mv, PHOTO_I2V_MIN_MOTION)
            if mv > best_motion:
                best_motion = mv
                shutil.copy(out_mp4, out_mp4.with_suffix(".best.mp4"))
            if mv >= PHOTO_I2V_MIN_MOTION:
                break
            if progress_cb and attempt + 1 < max_tries:
                progress_cb(f":repeat: {tag} 모션 부족({mv:.1f}) — 더 강하게 재생성")
        # Keep the best attempt if none cleared the bar.
        best_path = out_mp4.with_suffix(".best.mp4")
        if best_path.exists():
            if best_motion < PHOTO_I2V_MIN_MOTION:
                log.warning("photo_i2v %s: best motion only %.2f — using it anyway",
                            tag, best_motion)
                shutil.copy(best_path, out_mp4)
            try:
                best_path.unlink()
            except Exception:
                pass
        sources[tag] = {
            "source": str(out_mp4),
            "trim_start": 0.0,
            "trim_dur": float(seconds),
        }
        # PD 2026-06-08: per-cut Ryani marking gate (angle-aware) on the photo_i2v
        # output. If Ryani's blaze drifted (frontal + wrong), DISCARD and swap in a
        # real video clip — no Seedance distortion. (rf side of the per-cut gate.)
        if not dry_run:
            who = ""
            for ccx in (manifests.get("concept_cuts") or []):
                if (ccx.get("tag") or ccx.get("cut_tag")) == tag:
                    who = (ccx.get("who") or "").lower(); break
            if who in ("ryani", "leo", "both") and not _cut_character_ok(out_mp4, who):
                repl = _find_replacement_real_clip(who)
                if repl:
                    sources[tag] = repl
                    log.info("photo_i2v %s: Ryani drift → replaced with real clip %s",
                             tag, repl.get("asset_id"))
                    if progress_cb:
                        progress_cb(f":arrows_counterclockwise: {tag} 랴니 마킹 드리프트 "
                                    f"— 실제 영상으로 교체")
                elif progress_cb:
                    progress_cb(f":warning: {tag} 마킹 드리프트, 교체 클립 없음 — photo_i2v 유지")
        n_rendered += 1
    if n_rendered:
        log.info("photo_i2v: %d cuts pre-rendered", n_rendered)
    sources_path.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _prerender_split_cuts(manifests: dict, work_dir: Path,
                            progress_cb: ProgressCb = None,
                            dry_run: bool = False) -> None:
    """PD 2026-06-03: split_horizontal / split_vertical edit_effect.

    Each split cut has TWO assets (cut.asset_id + cut.secondary_asset_id).
    ffmpeg combines them into one 1080×1920 mp4 (the cut's source).
    Subsequent trim/caption-burn treats it as a single clip.

    split_horizontal: left + right side-by-side (each 540×1920)
    split_vertical:   top + bottom stacked (each 1080×960)
    """
    sources_path = Path(manifests["sources"])
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    split_dir = work_dir / "split_prerender"
    n_rendered = 0
    for tag, entry in sources.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("__needs_split_prerender__"):
            continue
        primary = entry.get("source", "")
        secondary = entry.get("secondary_source", "")
        effect = entry.get("edit_effect", "")
        if not (primary and secondary and Path(primary).exists() and Path(secondary).exists()):
            log.warning("split cut %s: missing primary/secondary source — falling back to primary only", tag)
            for k in ("secondary_source", "secondary_trim_start", "edit_effect",
                      "__needs_split_prerender__"):
                entry.pop(k, None)
            continue
        split_dir.mkdir(parents=True, exist_ok=True)
        ts_a = float(entry.get("trim_start", 0.0))
        ts_b = float(entry.get("secondary_trim_start", 0.0))
        dur = float(entry.get("trim_dur", 5.0))
        out_mp4 = split_dir / f"{tag}.mp4"
        if effect == "split_horizontal":
            # Each input scaled+padded to 540×1920, then hstacked.
            filt = (
                "[0:v]" + f"trim=start={ts_a}:duration={dur},setpts=PTS-STARTPTS,"
                "scale=540:1920:force_original_aspect_ratio=increase,"
                "crop=540:1920" + "[a];"
                "[1:v]" + f"trim=start={ts_b}:duration={dur},setpts=PTS-STARTPTS,"
                "scale=540:1920:force_original_aspect_ratio=increase,"
                "crop=540:1920" + "[b];"
                "[a][b]hstack=inputs=2[vout]"
            )
        else:  # split_vertical
            filt = (
                "[0:v]" + f"trim=start={ts_a}:duration={dur},setpts=PTS-STARTPTS,"
                "scale=1080:960:force_original_aspect_ratio=increase,"
                "crop=1080:960" + "[a];"
                "[1:v]" + f"trim=start={ts_b}:duration={dur},setpts=PTS-STARTPTS,"
                "scale=1080:960:force_original_aspect_ratio=increase,"
                "crop=1080:960" + "[b];"
                "[a][b]vstack=inputs=2[vout]"
            )
        cmd = [
            "ffmpeg", "-y", "-nostats", "-loglevel", "error",
            "-i", str(primary),
            "-i", str(secondary),
            "-filter_complex", filt,
            "-map", "[vout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "20",
            "-an",
            str(out_mp4),
        ]
        _run(cmd, f":left_right_arrow: split {tag} ({effect})",
             progress_cb, dry_run)
        # Replace cut's source with the pre-rendered split mp4. trim is
        # already applied in the filter so trim_start=0, trim_dur=dur.
        entry["source"] = str(out_mp4)
        entry["trim_start"] = 0.0
        entry["trim_dur"] = dur
        for k in ("secondary_source", "secondary_trim_start", "edit_effect",
                  "__needs_split_prerender__"):
            entry.pop(k, None)
        n_rendered += 1
    if n_rendered:
        log.info("split_screen: %d cuts pre-rendered", n_rendered)
    sources_path.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _prerender_chain_from_prev(manifests: dict, work_dir: Path,
                                 progress_cb: ProgressCb = None,
                                 dry_run: bool = False) -> None:
    """Real_footage Tier 3 (PD 2026-06-02): cuts marked
    `source = "__chain_from_prev__"` get rendered via Seedance i2v from the
    previous cut's last ffmpeg-extracted frame. Same chain mechanism as
    ai_vtuber but applied within real_footage. Requires sequential
    processing — depends on prev cut already resolved to a real mp4.
    """
    sources_path = Path(manifests["sources"])
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    cuts = manifests.get("cuts") or []
    work_anim = work_dir / "animated"
    work_anim.mkdir(parents=True, exist_ok=True)
    n_rendered = 0
    for i, item in enumerate(cuts):
        tag = item.get("tag")
        entry = sources.get(tag) or {}
        if not isinstance(entry, dict):
            continue
        if entry.get("source") != "__chain_from_prev__":
            continue
        if i == 0:
            log.warning("chain_from_prev on first cut — cannot chain")
            continue
        prev_tag = cuts[i - 1].get("tag")
        prev_entry = sources.get(prev_tag) or {}
        prev_src = prev_entry.get("source")
        if not prev_src or not Path(prev_src).exists():
            log.warning("chain_from_prev %s: prev mp4 unresolved", tag)
            continue
        prev_mp4 = Path(prev_src)
        chain_jpg = work_dir / f"_chain_rf_{tag}.jpg"
        _run([
            "ffmpeg", "-y", "-sseof", "-0.5", "-i", str(prev_mp4),
            "-frames:v", "1", "-q:v", "2", str(chain_jpg),
        ], f":link: [0/3] Chain anchor {tag} ← {prev_tag}",
            progress_cb, dry_run)
        if not chain_jpg.exists():
            continue
        out_mp4 = work_anim / f"{tag}.mp4"
        motion = entry.get("motion_prompt") or "gentle continuation motion"
        seconds = str(int(entry.get("seedance_seconds") or 5))
        cmd = [
            "python3", "scripts/animate_seedance_i2v.py",
            "--mode", "i2v",
            "--image", str(chain_jpg),
            "--prompt", motion,
            "--seconds", seconds,
            "--model", os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE),
            "--output", str(out_mp4),
        ]
        _run(cmd, f":film_frames: [0/3] Seedance chain {tag}",
             progress_cb, dry_run)
        sources[tag] = {
            "source": str(out_mp4),
            "trim_start": 0.0,
            "trim_dur": float(seconds),
        }
        n_rendered += 1
    if n_rendered:
        log.info("chain_from_prev: %d cuts pre-rendered", n_rendered)
    sources_path.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ensure_local(file_path: str, source_uuid: str | None) -> str | None:
    """PD 2026-06-07 efficient model: a clip's original may have been pruned to
    save space. If the file is missing but we have its Photos UUID, re-download
    it on demand to the same path. Returns a valid local path or None."""
    try:
        if file_path and Path(file_path).exists():
            return file_path
        if not source_uuid:
            return file_path  # nothing we can do — let caller handle missing
        from icloud.sync import download_asset_by_uuid
        dest = Path(file_path).parent if file_path else (ROOT / "data" / "assets" / "clips")
        dl = download_asset_by_uuid(source_uuid, dest)
        if not dl:
            return None
        # Move/rename the downloaded {uuid}.ext to the expected file_path.
        if file_path and Path(dl).resolve() != Path(file_path).resolve():
            try:
                shutil.move(dl, file_path)
                return file_path
            except Exception:
                return dl
        return dl
    except Exception as e:
        log.warning("ensure_local failed for %s: %s", file_path, e)
        return file_path if (file_path and Path(file_path).exists()) else None


def _trim_real_footage_clips(manifests: dict, anim_dir: Path,
                                progress_cb: ProgressCb = None,
                                dry_run: bool = False) -> None:
    """Trim each source clip to animated/<tag>.mp4 with per-cut edit_effect
    applied (PD 2026-06-02: ken_burns / zoom / pan / speed / freeze). The
    edit_effect comes from concept_cuts[i].edit_effect.

    PD 2026-06-02 also: if the LAST body cut's longest caption ends after
    the trim duration (caption would be cut short), auto-extend the clip
    via freeze_last_frame to caption_end + 0.8s — gives the final line 여운.
    """
    sources_path = Path(manifests["sources"])
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    concept_cuts = manifests.get("concept_cuts") or []
    cuts_meta = manifests.get("cuts") or []
    by_tag_effect = {}
    by_tag_crop = {}
    last_body_tag = None
    for i, item in enumerate(cuts_meta):
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        by_tag_effect[item.get("tag")] = (cc.get("edit_effect") or "static").strip().lower()
        # PD 2026-06-06: per-cut crop to push an unwanted element (esp. a
        # background human / 할머니 치마) out of frame. crop_out names WHERE the
        # unwanted thing is (top/bottom/left/right) — we zoom-crop AWAY from it.
        by_tag_crop[item.get("tag")] = (cc.get("crop_out") or "").strip().lower()
        if cc.get("function") != "wink_ending":
            last_body_tag = item.get("tag")  # tracks the last non-wink cut

    # Caption end-time per tag from captions manifest (for 여운 padding)
    cap_end_by_tag = {}
    try:
        cap_path = Path(manifests.get("captions") or "")
        if cap_path.exists():
            cap_data = json.loads(cap_path.read_text(encoding="utf-8"))
            for tag, body in cap_data.items():
                scenes = body.get("scenes") or []
                ends = [float(s.get("end", 0)) for s in scenes if s.get("end")]
                if ends:
                    cap_end_by_tag[tag] = max(ends)
    except Exception:
        pass

    anim_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for tag, entry in sources.items():
        if not isinstance(entry, dict):
            continue
        src = entry.get("source", "")
        if src in ("__interp_pending__", "__photo_i2v__", "__chain_from_prev__"):
            continue
        # PD 2026-06-07 efficient model: if the original was pruned, re-download
        # it on demand using its Photos UUID.
        if src and not Path(src).exists() and entry.get("source_uuid"):
            if progress_cb:
                progress_cb(f":arrow_down: {tag} 원본 재다운로드 (on-demand)")
            restored = _ensure_local(src, entry.get("source_uuid"))
            if restored:
                src = restored
        src_path = Path(src)
        if not src_path.exists():
            log.warning("trim: source missing for %s: %s", tag, src)
            continue
        out_mp4 = anim_dir / f"{tag}.mp4"
        try:
            if out_mp4.resolve() == src_path.resolve():
                continue
        except (OSError, RuntimeError):
            pass
        trim_start = float(entry.get("trim_start") or 0.0)
        trim_dur = float(entry.get("trim_dur") or 4.0)
        effect = by_tag_effect.get(tag, "static")
        # ★ PD 2026-06-06 ROOT-CAUSE FIX: ken_burns / zoom / pan use
        # `zoompan=...:d=dur*30`, which on a VIDEO freezes the FIRST frame and
        # only zooms — turning real footage into a "정지 화면 줌인" still. PD's
        # repeated complaint ("동영상을 이미지로 캡쳐해서 줌인만"). real_footage
        # already HAS motion, so drop the zoom/pan family entirely → play the
        # real clip (static framing). Keep speed_* and freeze (those don't
        # freeze-to-first-frame).
        _ZOOM_FAMILY = {"ken_burns", "zoom_in_slow", "zoom_out_slow",
                        "zoom_in", "zoom_out", "pan_left", "pan_right", "pan"}
        if effect in _ZOOM_FAMILY:
            log.info("real_footage %s: drop zoom effect '%s' → play real footage (static)",
                     tag, effect)
            effect = "static"
        # LAST body cut 여운 (PD 2026-06-08): play the real footage THROUGH the
        # caption (motion under the text), then FREEZE the final frame ~2s for a
        # quiet held ending — "캡션 뜨고 바로 끝나는 느낌"을 없앤다. The held end
        # is only the last ~2s (not the whole clip), so it reads as a deliberate
        # 여운 beat, not a "정지 화면".
        if tag == last_body_tag:
            cap_end = float(cap_end_by_tag.get(tag, 0))
            end_freeze = float(os.getenv("RF_END_FREEZE_S", "2.0"))
            src_dur = entry.get("src_dur")
            avail = (float(src_dur) - trim_start) if src_dur else None
            play = max(trim_dur, cap_end + 0.3)   # real footage at least to caption end
            if avail is not None:
                play = min(play, avail)
            trim_dur = play
            vf, time_args = _build_edit_effect_filter(
                "freeze_to_caption_end", trim_dur, extra_pad=end_freeze)
            effect = "freeze_to_caption_end"
            log.info("last cut %s — play %.1fs + end freeze hold %.1fs",
                     tag, play, end_freeze)
        else:
            vf, time_args = _build_edit_effect_filter(effect, trim_dur)

        # PD 2026-06-06 HARD RULE: a human FACE must NEVER be visible. If this
        # cut's clip has a human, crop them out. Prefer the VLM-computed
        # pets-only window (reliable — keeps pets, excludes the face); fall back
        # to the writer's directional crop_out hint, then a center zoom. Runs
        # BEFORE the edit_effect.
        crop_hint = by_tag_crop.get(tag, "")
        has_human = bool(entry.get("has_human"))
        crop_vf = ""
        if has_human or crop_hint:
            if not dry_run:
                crop_vf = _vlm_pet_crop_filter(
                    src_path, trim_start, float(entry.get("trim_dur") or 0.0))
            if not crop_vf:
                # fall back to directional hint, or a center zoom if a human is
                # present but no direction was given (never leave a face in).
                crop_vf = _build_crop_filter(crop_hint or ("center" if has_human else ""))
        if crop_vf:
            vf = ",".join(p for p in (crop_vf, vf) if p)
            log.info("crop %s — has_human=%s hint=%s → %s",
                     tag, has_human, crop_hint, crop_vf[:40])

        # PD 2026-06-08 ROTATION FIX: a rotated source (e.g. iPhone landscape with
        # rotation=-90) + a crop filter → ffmpeg crops the RAW unrotated frame and
        # leaves the rotation flag → sideways output ("갑자기 세로 화면"). Bake the
        # rotation with transpose BEFORE crop (so crop coords, computed on the
        # display-oriented frame, match) and disable autorotate to avoid doubling.
        rot = _probe_rotation(src_path)
        transpose_vf = ""
        if rot in (-90, 270):
            transpose_vf = "transpose=1"
        elif rot in (90, -270):
            transpose_vf = "transpose=2"
        elif rot in (180, -180):
            transpose_vf = "transpose=1,transpose=1"
        if transpose_vf:
            vf = ",".join(p for p in (transpose_vf, vf) if p)

        cmd = ["ffmpeg", "-y", "-nostats", "-loglevel", "error"]
        if transpose_vf:
            cmd.append("-noautorotate")
        cmd += [
            "-ss", f"{trim_start:.2f}",
            "-i", str(src_path),
            "-t", f"{trim_dur:.2f}",
        ]
        if vf:
            cmd.extend(["-filter:v", vf])
        cmd.extend([
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "20",
            "-an",
            "-movflags", "+faststart",
            str(out_mp4),
        ])
        label = f"Trim {tag} ({trim_dur:.1f}s, {effect})"
        _run(cmd, f":scissors: [1/3] {label}", progress_cb, dry_run)
        n += 1
    if progress_cb:
        progress_cb(f":scissors: [1/3] {n} clips trimmed → animated/")


def _vlm_pet_crop_filter(src_path: Path, trim_start: float = 0.0,
                         trim_dur: float = 0.0) -> str:
    """PD 2026-06-06 HARD RULE: a human FACE must NEVER be visible in
    real_footage. For a clip with a human, ask Gemini for the largest 9:16
    portrait window that contains the pet(s) and EXCLUDES every human (face
    especially). Return an ffmpeg crop filter, or "" if it can't be determined
    (caller then falls back to the directional crop hint)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return ""
    try:
        from google import genai as _genai
        from google.genai import types as _types
        frame = src_path.parent / f".cropprobe_{src_path.stem}.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-nostats", "-loglevel", "error",
            "-ss", f"{trim_start + 1.0:.2f}", "-i", str(src_path),
            "-frames:v", "1", str(frame),
        ], check=False)
        if not frame.exists():
            return ""
        data = frame.read_bytes()
        # PD 2026-06-07: bound the VLM call so a hung request can't freeze the
        # whole render (a 57-min hang killed a shakedown). Timeout → raises →
        # outer try returns "" (no crop, render continues).
        client = _genai.Client(api_key=api_key, http_options=_types.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")
        prompt = (
            "One frame of a vertical pet video. Locate, as fractions of the "
            "frame (0..1, top-left origin):\n"
            "1) 'pets' — ONE tight box covering ALL visible pets together (the "
            "orange cat and/or the black dog). Include every pet fully.\n"
            "2) 'human' — ONE box covering the visible human (whole person if "
            "seen; at minimum the head/face). Use zeros if truly no human.\n"
            "Return ONLY JSON: {\"pets\":{\"x\":..,\"y\":..,\"w\":..,\"h\":..},"
            "\"human\":{\"x\":..,\"y\":..,\"w\":..,\"h\":..}}."
        )
        resp = client.models.generate_content(
            model=model_name,
            contents=[
                _types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                prompt,
            ],
            config=_types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        box = json.loads((resp.text or "{}").strip())
        pets = box.get("pets") or {}
        human = box.get("human") or {}
        # PD 2026-06-08: the pets+human VLM call under-reports the FACE (it called a
        # bench man "lower body"), and a single probe frame misses a face that
        # appears LATER in the clip. Sample several frames across the trim window,
        # get a reliable face box for each, and UNION them — the static crop must
        # exclude the face wherever it appears. Use the union as the avoid-target.
        face_union = None  # (x,y,w,h) fractions
        try:
            from agents.facecheck import face_box as _face_box
            ts0 = trim_start
            span = trim_dur if trim_dur and trim_dur > 0 else 4.0
            offsets = [0.3, span * 0.5, max(0.3, span - 0.3)]
            seen_frac = []
            for k, off in enumerate(offsets):
                pf = src_path.parent / f".faceprobe_{src_path.stem}_{k}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{ts0 + off:.2f}", "-i", str(src_path),
                                "-frames:v", "1", str(pf)], check=False)
                if pf.exists():
                    fb = _face_box(pf)
                    if fb and float(fb.get("w", 0)) > 0 and float(fb.get("h", 0)) > 0:
                        seen_frac.append(fb)
                    try:
                        pf.unlink()
                    except Exception:
                        pass
            if seen_frac:
                x0 = min(float(f["x"]) for f in seen_frac)
                y0 = min(float(f["y"]) for f in seen_frac)
                x1 = max(float(f["x"]) + float(f["w"]) for f in seen_frac)
                y1 = max(float(f["y"]) + float(f["h"]) for f in seen_frac)
                face_union = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                human = face_union  # slide the window away from the whole face span
        except Exception:
            pass
        # ★ ROTATION FIX (PD 2026-06-06): use the EXTRACTED FRAME's dims
        # (rotation already applied) — the source stream reports unrotated dims.
        try:
            fprobe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                 str(frame)], capture_output=True, text=True, check=True)
            W, H = (int(v) for v in fprobe.stdout.strip().split("x")[:2])
        except Exception:
            W, H = 1080, 1920
        try:
            frame.unlink()
        except Exception:
            pass
        pw = float(pets.get("w", 0)); ph = float(pets.get("h", 0))
        if pw < 0.05 or ph < 0.05:
            log.warning("vlm crop: pets bbox not found for %s", src_path.name)
            return ""
        # Pets bbox in pixels, padded ~5% so we don't shave fur at the edge.
        pad = 0.05
        px = (float(pets.get("x", 0)) - pad) * W
        py = (float(pets.get("y", 0)) - pad) * H
        pwp = (pw + 2 * pad) * W
        php = (ph + 2 * pad) * H
        px = max(0.0, px); py = max(0.0, py)
        pwp = min(pwp, W - px); php = min(php, H - py)
        # PD 2026-06-06: the SMALLEST 9:16 window that fully CONTAINS the pets
        # (zoom OUT if needed) so pets are NEVER clipped. Then slide it away from
        # the human so the face drops out. This replaces the old "largest 9:16
        # inside a human-free box" which shrank onto the pets and cut them off.
        r = 9.0 / 16.0  # width / height
        if pwp / php >= r:        # pets wider than 9:16 → width drives it
            cw = pwp; ch = cw / r
        else:                      # pets taller → height drives it
            ch = php; cw = ch * r
        cw = min(cw, W); ch = min(ch, H)
        # keep aspect after clamping
        if cw / ch > r:
            cw = ch * r
        else:
            ch = cw / r
        # Allowed top-left range so the window still contains the pets bbox.
        x_lo = max(0.0, px + pwp - cw); x_hi = min(W - cw, px)
        y_lo = max(0.0, py + php - ch); y_hi = min(H - ch, py)
        if x_lo > x_hi:
            x_lo = x_hi = min(max(px + pwp / 2 - cw / 2, 0.0), W - cw)
        if y_lo > y_hi:
            y_lo = y_hi = min(max(py + php / 2 - ch / 2, 0.0), H - ch)
        # Slide AWAY from the human: among the allowed top-left range, pick the
        # endpoint whose window CENTER is farther from the human bbox center.
        def _far(lo, hi, dim, h_x, h_w, size):
            if hi <= lo:
                return lo
            if h_w <= 0:
                return (lo + hi) / 2.0
            hc = (h_x + h_w / 2.0) * dim
            c_lo = lo + size / 2.0
            c_hi = hi + size / 2.0
            return lo if abs(c_lo - hc) >= abs(c_hi - hc) else hi
        cx = _far(x_lo, x_hi, W, float(human.get("x", 0)), float(human.get("w", 0)), cw)
        cy = _far(y_lo, y_hi, H, float(human.get("y", 0)), float(human.get("h", 0)), ch)
        cw = int(cw) // 2 * 2; ch = int(ch) // 2 * 2
        cx = min(max(0, int(cx)), W - cw); cy = min(max(0, int(cy)), H - ch)
        # HARD vertical exclusion of the face union (PD 2026-06-08): the slide can
        # leave a face partly inside the window when it overlaps the pets' band.
        # Force the window fully below the face bottom (or fully above its top),
        # whichever fits — exclude the face even at the cost of cropping some pet
        # (the no-face rule wins). If neither fits, leave it (post-render check +
        # slot-skip is the final guard).
        if face_union:
            fx = float(face_union["x"]) * W; fy = float(face_union["y"]) * H
            fw = float(face_union["w"]) * W; fh = float(face_union["h"]) * H
            overlap = not (cy >= fy + fh or cy + ch <= fy or cx >= fx + fw or cx + cw <= fx)
            if overlap:
                below = int(fy + fh)            # window top at face bottom
                above = int(fy - ch)            # window bottom at face top
                if below + ch <= H:
                    cy = max(0, below)
                elif above >= 0:
                    cy = min(above, H - ch)
                cy = min(max(0, int(cy)), H - ch)
        return f"crop={cw}:{ch}:{cx}:{cy}"
    except Exception as ex:
        log.warning("vlm crop failed for %s: %s", src_path.name, ex)
        return ""


def _build_crop_filter(crop_out: str, zoom: float = 1.3) -> str:
    """PD 2026-06-06: zoom-crop a clip to push an unwanted element (usually a
    background human / 할머니 치마) out of frame. `crop_out` names WHERE the
    unwanted thing is; we keep a zoomed-in window biased to the OPPOSITE side.
    Aspect is preserved (window is the source aspect / zoom), so the downstream
    9:16 scale stays clean. Returns "" when no crop requested."""
    d = (crop_out or "").strip().lower()
    if d in ("", "none", "false", "no"):
        return ""
    w = f"floor(iw/{zoom}/2)*2"
    h = f"floor(ih/{zoom}/2)*2"
    if d == "top":          # human at top → keep bottom
        x, y = "(iw-ow)/2", "ih-oh"
    elif d == "bottom":     # human at bottom → keep top
        x, y = "(iw-ow)/2", "0"
    elif d == "left":       # human at left → keep right
        x, y = "iw-ow", "(ih-oh)/2"
    elif d == "right":      # human at right → keep left
        x, y = "0", "(ih-oh)/2"
    else:                   # "center"/generic → just zoom in to drop edges
        x, y = "(iw-ow)/2", "(ih-oh)/2"
    return f"crop={w}:{h}:{x}:{y}"


def _build_edit_effect_filter(effect: str, dur: float, extra_pad: float = 0.0) -> tuple[str, list]:
    """Translate Writer's edit_effect tag into an ffmpeg -filter:v expression.
    Returns (filter_string, extra_args). Empty string = no extra filter.
    `extra_pad` (PD 2026-06-02): seconds of freeze_last_frame to append for
    여운 on the final cut when the caption exceeds the trimmed clip length."""
    e = (effect or "static").strip().lower()
    if e == "freeze_to_caption_end":
        # Pad final frame for `extra_pad` sec (여운). PD 2026-06-08: FADE OUT during
        # the held frame so the ending breathes instead of cutting hard (and it
        # softly hides any leftover crop edge). Fade starts when the freeze begins.
        if extra_pad and extra_pad > 0.1:
            fade_d = min(extra_pad, max(0.6, extra_pad - 0.2))
            fade_st = max(0.0, dur + extra_pad - fade_d)
            return (f"tpad=stop_mode=clone:stop_duration={extra_pad:.2f},"
                    f"fade=t=out:st={fade_st:.2f}:d={fade_d:.2f}"), []
        return f"tpad=stop_mode=clone:stop_duration={extra_pad:.2f}", []
    if e in ("", "static", "none"):
        return "", []
    # Speed (uses setpts which doesn't preserve audio sync — but we already strip audio)
    if e.startswith("speed_"):
        try:
            mul = float(e.replace("speed_", "").replace("x", ""))
        except ValueError:
            mul = 1.0
        # PD 2026-06-07: fast-forward was TOO fast — clamp to a gentle max so the
        # action stays readable (1.3x felt frantic; ~1.2x reads better).
        mul = min(mul, float(os.getenv("RF_MAX_SPEED", "1.2")))
        if mul <= 1.0:
            return "", []
        return f"setpts={1.0/mul:.3f}*PTS", []
    # Ken Burns: gentle zoom-in + slight pan over the clip duration
    if e == "ken_burns":
        # zoompan generates frames at fps from a still — for video, use simple zoom
        return f"zoompan=z='min(zoom+0.0008,1.15)':d={int(dur*30)}:s=1080x1920,scale=1080:1920", []
    if e == "zoom_in_slow":
        return f"zoompan=z='min(zoom+0.0012,1.25)':d={int(dur*30)}:s=1080x1920,scale=1080:1920", []
    if e == "zoom_out_slow":
        return f"zoompan=z='if(lte(zoom,1.0),1.25,zoom-0.0012)':d={int(dur*30)}:s=1080x1920,scale=1080:1920", []
    if e == "pan_left":
        return f"zoompan=z=1.15:x='if(lte(on,1),iw-iw/zoom,x-2)':y='ih/2-(ih/zoom/2)':d={int(dur*30)}:s=1080x1920", []
    if e == "pan_right":
        return f"zoompan=z=1.15:x='if(lte(on,1),0,x+2)':y='ih/2-(ih/zoom/2)':d={int(dur*30)}:s=1080x1920", []
    if e == "freeze_last_frame":
        # tpad freezes last frame for 0.5s
        return f"tpad=stop_mode=clone:stop_duration=0.5", []
    # Unknown effect — log + skip
    log.warning("unknown edit_effect '%s' — using static", effect)
    return "", []


def _prune_missing_cuts(manifests: dict, anim_dir: Path,
                        progress_cb: ProgressCb = None) -> None:
    """Drop cuts whose animated/<tag>.mp4 doesn't exist from the caption manifest
    (and cuts/concept_cuts) so burn + assemble only see rendered cuts. Prevents a
    single missing clip (pruned photo_i2v source, failed trim) from crashing the
    whole episode. Raises if NOTHING remains."""
    cap_path = Path(manifests.get("captions", ""))
    if not cap_path.exists():
        return
    try:
        caps = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception:
        return
    dropped = []
    for tag in list(caps.keys()):
        if tag.startswith("_"):
            continue
        if not (anim_dir / f"{tag}.mp4").exists():
            caps.pop(tag, None)
            dropped.append(tag)
    if not dropped:
        return
    # keep at least one real cut
    remaining = [t for t in caps if not t.startswith("_")]
    if not remaining:
        raise RuntimeError(f"all cuts missing animated mp4 ({dropped}) — cannot render")
    cap_path.write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
    # also prune cuts/concept_cuts lists so downstream is consistent
    for key in ("cuts", "concept_cuts"):
        lst = manifests.get(key)
        if isinstance(lst, list):
            manifests[key] = [c for c in lst
                              if (c.get("tag") or c.get("cut_tag")) not in dropped]
    log.warning("pruned %d missing cut(s) before burn: %s", len(dropped), dropped)
    if progress_cb:
        progress_cb(f":scissors: 누락 컷 {len(dropped)}개 드롭(파일 없음) — 남은 컷으로 진행: {', '.join(dropped)}")


def run_real_footage_pipeline(manifests: dict, work_dir: Path,
                              progress_cb: ProgressCb = None,
                              dry_run: bool = False) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Step 0a: pre-render Seedance interp gap-fill cuts (legacy).
    _prerender_interp_fills(manifests, work_dir, progress_cb, dry_run)
    # Step 0b: pre-render Tier 2 photo→i2v cuts (PD 2026-06-02 real_footage v2).
    _prerender_photo_i2v_cuts(manifests, work_dir, progress_cb, dry_run)
    # Step 0c: pre-render Tier 3 chain-from-prev cuts (after 0a/0b resolved).
    _prerender_chain_from_prev(manifests, work_dir, progress_cb, dry_run)
    # Step 0d: pre-render split_horizontal / split_vertical cuts (PD 2026-06-03).
    _prerender_split_cuts(manifests, work_dir, progress_cb, dry_run)

    # Step 1: trim source clips into animated/ (PD 2026-06-02: split trim
    # from caption burn so VLM rewrite can run between them).
    anim_dir = work_dir / "animated"
    _trim_real_footage_clips(manifests, anim_dir, progress_cb, dry_run)

    # Step 1b: VLM post-render check + caption rewrite (same agent as ai_vtuber).
    # PD 2026-06-06 ROOT CAUSE FIX: for single-pass real_footage, the captions
    # are ALREADY grounded in clip ground truth by realfootage_concept.md. Re-
    # running the Caption Agent here silently overwrote every upstream prompt fix
    # (주체정확성/랴니대사/여운/가독성) — the reason PD's feedback "wasn't applied"
    # for days. Skip the rewrite when the concept came from the single-pass
    # author. Override with RF_FORCE_VLM_REWRITE=1 for debugging.
    concept_author = (manifests.get("concept") or {}).get("author", "")
    is_singlepass = concept_author == "realfootage_singlepass"
    force_rewrite = os.environ.get("RF_FORCE_VLM_REWRITE") == "1"
    if is_singlepass and not force_rewrite:
        log.info("real_footage single-pass: SKIP VLM caption rewrite "
                 "(captions already grounded)")
        if progress_cb:
            progress_cb(":lock: [1b/3] 단일-패스 캡션 보존 — VLM 재작성 건너뜀")
    else:
        cuts_local = manifests.get("cuts") or []
        concept_cuts_local = manifests.get("concept_cuts") or []
        try:
            _vlm_post_render_caption_rewrite(
                work_dir, manifests, cuts_local, concept_cuts_local, anim_dir,
                progress_cb=progress_cb, dry_run=dry_run,
            )
        except Exception as ex:
            log.warning("VLM rewrite skipped for real_footage: %s", ex)

    # PD 2026-06-08: a cut whose animated/<tag>.mp4 is missing (e.g. a photo_i2v
    # cut whose source photo was pruned and couldn't be re-fetched) used to crash
    # the burn step (rc=1). Drop missing cuts from the caption manifest so the
    # episode renders with the available cuts instead of failing outright.
    if not dry_run:
        _prune_missing_cuts(manifests, anim_dir, progress_cb)

    # Step 1c: burn captions on trimmed clips.
    captioned_dir = ROOT / "data" / "output" / "animated_captioned"
    _run(
        _burn_captions_cmd(manifests, anim_dir, captioned_dir),
        ":speech_balloon: [1c/3] Burning captions (post-VLM)",
        progress_cb, dry_run,
    )

    # Step 2: ensure bumpers exist
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            ["python3", "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            ":loud_sound: [2/3] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(":loud_sound: [2/3] Bumpers exist — skip")

    # Step 3: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_rf_{ts}.mp4"
    _run(
        ["python3", "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--intro-bumper", str(INTRO_BUMPER),
         "--outro-bumper", str(OUTRO_BUMPER),
         "--music", manifests.get("bgm", str(DEFAULT_BGM)),
         "--out", str(out)],
        ":clapper: [3/3] Final assembly",
        progress_cb, dry_run,
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Pipeline: cartoon_sticker
# ──────────────────────────────────────────────────────────────────────
def run_cartoon_sticker_pipeline(manifests: dict, card: dict, work_dir: Path,
                                 progress_cb: ProgressCb = None,
                                 dry_run: bool = False) -> Path:
    """Cartoon sticker lane: same as ai_vtuber but with Korean cartoon style prompts.

    Pipeline: preprocess → AI regen (cartoon style) → Veo i2v → captions → assemble
    Uses regen_vtuber_style.py with cartoon-specific prompts — NOT decorate_photo.py (PIL).
    """
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    cuts = manifests["cuts"]
    payload = json.loads(card.get("payload_json", "{}"))
    theme = card.get("theme", "")

    input_dir = work_dir / "input"
    regen_dir = work_dir / "regen"
    anim_dir = ROOT / "data" / "output" / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)

    # Generate cartoon-style regen prompts if not already in manifests
    if "regen_prompts" not in manifests:
        preserve = (
            "The pet's breed, fur color, markings, eye color, and body proportions "
            "MUST be preserved exactly. Do NOT alter the pet's appearance."
        )
        cartoon_style = (
            "Korean webtoon cartoon style illustration, cute kawaii aesthetic, "
            "pastel colors, sparkles, small hearts and stars floating around, "
            "soft glow effects, clean outlines, adorable pet portrait"
        )
        regen = {
            "_base_style": cartoon_style,
            "_preserve_subjects": preserve,
        }
        for item in cuts:
            subjects = item.get("asset", {}).get("subjects_csv", "pet")
            regen[item["tag"]] = (
                f"{cartoon_style}, featuring {subjects} in a {theme} scene. {preserve}"
            )
        regen_path = work_dir / "regen_prompts.json"
        regen_path.write_text(json.dumps(regen, ensure_ascii=False, indent=2), encoding="utf-8")
        manifests["regen_prompts"] = str(regen_path)

    # Step 1: preprocess photos
    _run(
        ["python3", "scripts/preprocess_for_i2v.py",
         "--manifest", manifests["sources"],
         "--out-dir", str(input_dir)],
        ":gear: [1/6] Preprocessing photos",
        progress_cb, dry_run,
    )

    # Step 2: AI regen with cartoon style
    if progress_cb:
        progress_cb(":art: [2/6] AI 캐릭터 생성 시작...")
    if not dry_run:
        from scripts.generate_character_scene import generate_batch
        failures = generate_batch(
            Path(manifests["regen_prompts"]),
            input_dir if input_dir.exists() else None,
            regen_dir,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            progress_cb=progress_cb,
        )
        if failures:
            total = len([k for k in json.loads(Path(manifests["regen_prompts"]).read_text()).keys() if not k.startswith("_")])
            success = total - failures
            min_required = max(4, int(total * 0.75))
            if success < min_required:
                raise RuntimeError(f"AI 캐릭터 생성 {failures}/{total}건 실패 (최소 {min_required}컷 필요)")
            log.warning("AI 캐릭터 생성 %d/%d 실패 — 성공한 %d컷으로 진행", failures, total, success)
    else:
        log.info("[dry-run] would generate character scenes")

    # Step 3: Veo i2v per cut
    for item in cuts:
        tag = item["tag"]
        regen_png = regen_dir / f"{tag}.png"
        out_mp4 = anim_dir / f"{tag}.mp4"
        prompt = manifests.get("motion_prompts", {}).get(tag,
            "gentle natural motion, slow blink, slight head movement, soft breathing")
        _run(
            ["python3", "scripts/animate_hero_veo3_vertex.py",
             "--image", str(regen_png),
             "--prompt", prompt,
             "--seconds", "4",
             "--model", os.getenv("VEO_MODEL", "veo-3.0-generate-001"),
             "--output", str(out_mp4)],
            f":film_frames: [3/6] Veo i2v {tag}",
            progress_cb, dry_run,
        )

    # Step 4: build bumpers if needed
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            ["python3", "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            ":loud_sound: [4/6] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(":loud_sound: [4/6] Bumpers exist — skip")

    # Step 5: burn captions (손글씨 기본, Director font_override 가능)
    _run(
        _burn_captions_cmd(manifests, anim_dir, ROOT / "data" / "output" / "animated_captioned"),
        ":speech_balloon: [5/6] Burning captions",
        progress_cb, dry_run,
    )

    # Step 6: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_cs_{ts}.mp4"
    _run(
        ["python3", "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--intro-bumper", str(INTRO_BUMPER),
         "--outro-bumper", str(OUTRO_BUMPER),
         "--music", manifests.get("bgm", str(DEFAULT_BGM)),
         "--out", str(out)],
        ":clapper: [6/6] Final assembly",
        progress_cb, dry_run,
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Pipeline: ai_vtuber

def _review_script_before_render(veo_prompts: dict, manifests: dict,
                                progress_cb: ProgressCb = None) -> None:
    """Cameraman reviews Producer's script before rendering.

    Reviews and auto-fixes:
    - Missing Ryani marking description → auto-inject
    - Safety filter trigger words → warn
    - Inconsistent backgrounds between scenes → warn
    - Empty or too-short prompts → warn
    - Writes back fixed prompts to file
    """
    issues = []
    ryani_marking_phrase = "Boston Terrier"  # key phrase from optimized prompt

    for tag, vp in veo_prompts.items():
        prompt = vp.get("prompt", "") if isinstance(vp, dict) else ""
        if not prompt:
            continue

        # Check 1: Ryani scenes must have marking description
        # Producer saves tokens by only describing Ryani fully in scene 1.
        # Cameraman auto-injects the full description into later scenes.
        if "french bulldog" in prompt.lower() or "ryani" in prompt.lower():
            if ryani_marking_phrase.lower() not in prompt.lower():
                ryani_desc = (
                    " (Ryani: old black French Bulldog, age 11. "
                    "White markings on black face: thin Boston Terrier-style white blaze (NARROW line, not the typical wide splash) from nose to forehead, "
                    "white dot above left eye, white dot above right eye. Silver-grey aged muzzle. "
                    "White chin. White chest patch. Bat ears. No tail. Only black, white, grey.)"
                )
                if isinstance(vp, dict):
                    vp["prompt"] = prompt + ryani_desc
                    log.info("Auto-injected Ryani markings into %s", tag)

        # Check 2: Safety filter words
        safety_words = ["sprawled", "rises and falls", "spread legs", "rear end raised"]
        for sw in safety_words:
            if sw in prompt.lower():
                issues.append(f"{tag}: safety filter 위험 단어 '{sw}'")

        # Check 3: Too short prompt = lazy writing
        if len(prompt) < 150:
            issues.append(f"{tag}: 프롬프트 {len(prompt)}자 — 150자 미만은 디테일 부족")

        # Check 4: Objects should have context (warn only, don't auto-fix)
        # Producer should handle this via set library + props

    # Check 4: Background consistency — first scene's background should be repeated
    bg_keywords = []
    for tag, vp in veo_prompts.items():
        prompt = vp.get("prompt", "") if isinstance(vp, dict) else ""
        # Extract location keywords
        for kw in ["Korean apartment", "living room", "kitchen", "bedroom", "blue sofa", "wooden floor"]:
            if kw.lower() in prompt.lower():
                bg_keywords.append((tag, kw))
    if bg_keywords:
        first_bg = set(kw for t, kw in bg_keywords if t == list(veo_prompts.keys())[0])
        for tag, kw in bg_keywords:
            if tag != list(veo_prompts.keys())[0] and kw not in [k for t, k in bg_keywords if t == tag]:
                pass  # Background mismatch tracking (for now just log)

    # ── Prompt Quality Score (like MSE for marking) ──
    prompt_scores = []
    for tag, vp in veo_prompts.items():
        prompt = vp.get("prompt", "") if isinstance(vp, dict) else ""
        if not prompt:
            continue
        score = 0
        checks = {}
        # Length (max 3 points)
        checks["length"] = min(3, len(prompt) // 100)
        score += checks["length"]
        # Has camera angle (1 point)
        cam_words = ["close-up", "wide shot", "medium shot", "low-angle", "overhead", "eye-level"]
        checks["camera"] = 1 if any(w in prompt.lower() for w in cam_words) else 0
        score += checks["camera"]
        # Has lighting (1 point)
        light_words = ["warm light", "afternoon", "morning", "golden", "lamplight", "natural light"]
        checks["lighting"] = 1 if any(w in prompt.lower() for w in light_words) else 0
        score += checks["lighting"]
        # Has specific action (1 point) — not just "sits" or "stands"
        action_words = ["walks", "jumps", "grabs", "sniffs", "nudges", "snatches", "runs",
                        "crouches", "wiggles", "kneads", "licks", "blinks", "tilts"]
        checks["action"] = 1 if any(w in prompt.lower() for w in action_words) else 0
        score += checks["action"]
        # Has real background detail (1 point)
        bg_words = ["sofa", "wooden floor", "kitchen", "counter", "window", "curtain", "bookshelf"]
        checks["background"] = 1 if any(w in prompt.lower() for w in bg_words) else 0
        score += checks["background"]
        # Has character marking (1 point for Ryani scenes)
        if "french bulldog" in prompt.lower() or "ryani" in prompt.lower():
            checks["marking"] = 1 if "Boston Terrier" in prompt else 0
            score += checks["marking"]
        # Max 8 points
        prompt_scores.append({"tag": tag, "score": score, "checks": checks})

    if prompt_scores:
        avg = sum(s["score"] for s in prompt_scores) / len(prompt_scores)
        if progress_cb:
            progress_cb(f":bar_chart: 프롬프트 품질: {avg:.1f}/8 (길이+카메라+조명+동작+배경+마킹)")
            for ps in prompt_scores:
                if ps["score"] < 5:
                    fails = [k for k, v in ps["checks"].items() if v == 0]
                    progress_cb(f"  {ps['tag']}: {ps['score']}/8 — 부족: {', '.join(fails)}")

    if issues and progress_cb:
        progress_cb(f":clipboard: 스크립트 검수: {len(issues)}건 발견, 자동 수정 적용")
        for issue in issues[:3]:
            progress_cb(f"  - {issue}")

    # Write back modified prompts to file so Veo script reads the updated version
    if manifests.get("veo_prompts"):
        veo_path = Path(manifests["veo_prompts"])
        veo_path.write_text(json.dumps(veo_prompts, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Updated veo_prompts.json with script review fixes")


def run_ai_vtuber_pipeline(manifests: dict, card: dict, work_dir: Path,
                           progress_cb: ProgressCb = None,
                           dry_run: bool = False) -> Path:
    # Route to text-to-video or image-to-video pipeline
    if manifests.get("generation_mode") == "text_to_video":
        return _run_t2v_pipeline(manifests, card, work_dir, progress_cb, dry_run)
    return _run_i2v_pipeline(manifests, card, work_dir, progress_cb, dry_run)


def _run_t2v_pipeline(manifests: dict, card: dict, work_dir: Path,
                      progress_cb: ProgressCb = None,
                      dry_run: bool = False) -> Path:
    """Text-to-video pipeline: Veo generates video from text prompt alone.

    No image preprocessing or GPT character generation needed.
    Steps: Veo t2v per scene → bumpers → captions → assemble.
    """
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    cuts = manifests["cuts"]
    # Use run-specific animated dir to avoid stale file conflicts
    anim_dir = work_dir / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)

    # Load veo prompts
    veo_prompts = {}
    if manifests.get("veo_prompts"):
        veo_prompts = json.loads(Path(manifests["veo_prompts"]).read_text(encoding="utf-8"))

    # Pre-render script review — Cameraman checks Producer's work
    _review_script_before_render(veo_prompts, manifests, progress_cb)

    n_scenes = len(cuts)
    total_steps = n_scenes + 3  # scenes + bumpers + captions + assemble

    # Step 1..N: Veo text-to-video per scene (partial failure allowed)
    failed_scenes = []
    succeeded_scenes = []
    for idx, item in enumerate(cuts, 1):
        tag = item["tag"]
        out_mp4 = anim_dir / f"{tag}.mp4"
        vp = veo_prompts.get(tag, {})
        prompt = vp.get("prompt", item.get("veo_prompt", ""))
        seconds = str(vp.get("seconds", item.get("duration_seconds", 4)))

        if not prompt:
            log.warning("No veo_prompt for %s — skipping", tag)
            failed_scenes.append(tag)
            continue

        # Skip if already generated (e.g. retry after partial failure)
        if out_mp4.exists() and out_mp4.stat().st_size > 10000:
            log.info("Scene %s already exists (%d bytes) — skipping", tag, out_mp4.stat().st_size)
            if progress_cb:
                progress_cb(f":fast_forward: [{idx}/{total_steps}] {tag} exists — skip")
            succeeded_scenes.append(tag)
            continue

        # Final check: log if Ryani prompt has marking keywords
        if "ryani" in prompt.lower() or "french bulldog" in prompt.lower():
            has_boston = "Boston Terrier" in prompt
            has_blaze = "blaze" in prompt.lower()
            if not has_boston:
                log.warning("⚠ %s: Ryani scene WITHOUT 'Boston Terrier' marking!", tag)
            log.info("%s: Ryani marking check: Boston=%s blaze=%s len=%d",
                     tag, has_boston, has_blaze, len(prompt))

        cmd = [
            "python3", "scripts/animate_hero_veo3_vertex.py",
            "--prompt", prompt,
            "--seconds", seconds,
            "--aspect", "9:16",
            "--model", os.getenv("VEO_MODEL", "veo-3.0-generate-001"),
            "--output", str(out_mp4),
        ]
        try:
            _run(cmd,
                 f":movie_camera: [{idx}/{total_steps}] Veo t2v {tag} ({seconds}s)",
                 progress_cb, dry_run)
            succeeded_scenes.append(tag)
        except RuntimeError as e:
            err_str = str(e)
            is_safety = "sensitive words" in err_str or "Responsible AI" in err_str
            if is_safety:
                log.warning("Veo SAFETY FILTER on %s — prompt needs rewording", tag)
                failed_scenes.append(tag)
                if progress_cb:
                    progress_cb(
                        f":no_entry: {tag} safety filter 거부 — "
                        f"이 씬의 동작 묘사에 선정적 표현이 포함됨. "
                        f"retry_loop에서 프롬프트 수정 후 재시도 필요")
            else:
                log.warning("Veo t2v failed for %s: %s", tag, err_str[:200])
                failed_scenes.append(tag)
                if progress_cb:
                    progress_cb(f":warning: {tag} 실패 ({err_str[:80]})")

    if not succeeded_scenes and not dry_run:
        raise RuntimeError(f"All {len(cuts)} scenes failed: {failed_scenes}")

    min_required = max(2, int(len(cuts) * 0.5))
    if len(succeeded_scenes) < min_required and not dry_run:
        raise RuntimeError(
            f"Too few scenes succeeded: {len(succeeded_scenes)}/{len(cuts)} "
            f"(min {min_required}). Failed: {failed_scenes}")

    if failed_scenes and progress_cb:
        progress_cb(f":warning: {len(failed_scenes)}개 씬 실패, {len(succeeded_scenes)}개로 진행")

    # Remove failed scenes from captions manifest so burn_captions doesn't look for them
    if failed_scenes and manifests.get("captions"):
        cap_path = Path(manifests["captions"])
        if cap_path.exists():
            cap_data = json.loads(cap_path.read_text(encoding="utf-8"))
            for ftag in failed_scenes:
                cap_data.pop(ftag, None)
            cap_path.write_text(json.dumps(cap_data, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Removed %d failed scenes from captions manifest", len(failed_scenes))

    # Bumpers
    step_bump = n_scenes + 1
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            ["python3", "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            f":loud_sound: [{step_bump}/{total_steps}] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(f":loud_sound: [{step_bump}/{total_steps}] Bumpers exist — skip")

    # Burn captions
    captioned_dir = work_dir / "animated_captioned"
    captioned_dir.mkdir(parents=True, exist_ok=True)
    step_cap = n_scenes + 2
    _run(
        _burn_captions_cmd(manifests, anim_dir, captioned_dir),
        f":speech_balloon: [{step_cap}/{total_steps}] Burning captions",
        progress_cb, dry_run,
    )

    # Assemble
    step_asm = n_scenes + 3
    out = ROOT / "data" / "output" / "episodes" / f"episode_t2v_{ts}.mp4"
    _run(
        ["python3", "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--in-dir", str(captioned_dir),
         "--intro-bumper", str(INTRO_BUMPER),
         "--outro-bumper", str(OUTRO_BUMPER),
         "--music", manifests.get("bgm", str(DEFAULT_BGM)),
         "--out", str(out)],
        f":clapper: [{step_asm}/{total_steps}] Final assembly",
        progress_cb, dry_run,
    )
    return out


_RYANI_MARKING_EMPHASIS = (
    " CRITICAL — Ryani the black French Bulldog must keep her exact markings every "
    "frame: a THIN narrow white Boston-Terrier-style blaze (a fine line, NOT a wide "
    "splash, do NOT enlarge it) from nose up the forehead, small white dot above "
    "each eye, silver-grey aged muzzle, white chin, white chest patch, bat ears, NO "
    "tail. Only black/white/grey — no brown. Keep the blaze thin and the face "
    "identical to the input; do not redraw or distort her markings.")

_LEO_MARKING_EMPHASIS = (
    " CRITICAL — Leo the orange tabby cat must look like the REAL cat, not AI-"
    "generated: pale yellow-green / chartreuse eyes (NOT gold or amber), white chin "
    "tuft, lean young-adult body, natural real-cat face and proportions. Do not "
    "warp, plasticize, or redraw his face.")


def _cut_character_ok(mp4_path: Path, who: str = "both", n_frames: int = 3) -> bool:
    """PD 2026-06-08: per-cut CHARACTER gate for i2v output (av + rf photo_i2v),
    checked RIGHT AFTER the cut renders. Ryani/Leo markings (incl. a dedicated
    blaze-thickness question) + AI-distortion. ANGLE-AWARE: a side profile hiding
    markings is FINE; fail only when a pet is frontal/clear AND clearly wrong.
    Scene/intent defects are a SEPARATE focused call (`_cut_scene_ok`) — bundling
    dilutes attention (the facecheck lesson). Fail-open (True) on error / no API.
    Returns True = keep, False = bad."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        import tempfile as _tf
        dur = 5.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4_path)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 5.0)
        except Exception:
            pass
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        parts = []
        with _tf.TemporaryDirectory() as td:
            for i in range(n_frames):
                t = dur * (i + 0.5) / n_frames
                fp = Path(td) / f"m{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path),
                                "-frames:v", "1", str(fp)], check=False, timeout=20)
                if fp.exists():
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return True
            w = (who or "both").lower()
            specs = []
            if w in ("ryani", "both"):
                specs.append(
                    "RYANI (black French Bulldog): THIN white blaze (narrow line "
                    "nose→forehead, NOT thick/large), white eyebrow dots, silver-grey "
                    "muzzle, white chin, white chest patch, bat ears, NO tail, only "
                    "black/white/grey (no brown).")
            if w in ("leo", "both"):
                specs.append(
                    "LEO (orange tabby cat): pale YELLOW-GREEN/chartreuse eyes (NOT "
                    "gold/amber), white chin tuft, lean young-adult body, natural "
                    "real-cat look. (A nose scar is NOT required — current Leo has a "
                    "faint one but baby/kitten Leo had none; do not fail on the scar.)")
            ask_blaze = w in ("ryani", "both")
            blaze_q = (
                " SEPARATELY and CAREFULLY, judge ONE specific Ryani defect that a "
                "holistic look misses: the white BLAZE down her face. Correct = a THIN "
                "NARROW stripe/line from nose up between the eyes. DEFECT = the blaze is "
                "THICK, WIDE, BROAD, or covers a large area of the muzzle/forehead "
                "(Seedance commonly over-widens it). Set blaze_too_thick=true ONLY when "
                "Ryani's face is frontal enough to see the blaze AND it is clearly too "
                "thick/wide; otherwise false (side profile / not visible = false)."
            ) if ask_blaze else ""
            blaze_field = ",\"blaze_too_thick\":true|false" if ask_blaze else ""
            prompt = (
                "These frames are from ONE rendered cut. Judge the pet CHARACTER "
                "fidelity for: " + " ".join(specs) + " IMPORTANT: a side profile or "
                "turned-away face that naturally hides markings is FINE — not a "
                "defect. Flag a character problem ONLY when a pet is frontal/clearly "
                "visible AND its markings/features are clearly wrong, OR a pet looks "
                "obviously AI-distorted (warped face, melted/extra features, wrong "
                "proportions, plastic/fake look)." + blaze_q +
                " Return ONLY JSON: {\"clear\":true|false,\"character_ok\":true|false"
                + blaze_field + "}.")
            contents = list(parts) + [prompt]
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=contents,
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            import json as _json
            d = _json.loads((resp.text or "{}").strip())
            clear = bool(d.get("clear", d.get("frontal")))
            ok = bool(d.get("character_ok", d.get("markings_ok")))
            blaze_bad = bool(d.get("blaze_too_thick")) if ask_blaze else False
            bad = (clear and not ok) or blaze_bad
            if bad:
                why = "BLAZE-too-thick" if blaze_bad else "drift/generative"
                log.info("cut character gate: %s %s in %s", w, why, Path(mp4_path).name)
            return not bad
    except Exception as e:
        log.warning("cut marking check failed (%s) — keeping cut", e)
        return True


def _cut_scene_ok(mp4_path: Path, scene_ref_path=None, expected_facts: str = "",
                  n_frames: int = 3) -> bool:
    """PD 2026-06-08: per-cut SCENE gate — a DEDICATED, focused VLM call (separate
    from the character gate, because bundling many questions dilutes attention and
    the model defaults to 'fine' — verified: bundled missed the floor-sink, focused
    caught it). Two dimensions, both judged on the actual render so it catches what a
    pre-render text check can't:
      (B) UNIVERSAL defect — open-ended 'is there an OBVIOUS real-world-impossible
          defect?' (melted/floating objects, duplicated furniture, impossible limbs).
      (C) INTENT match — compare the render against the room reference photo +
          authoritative set facts; flag a CLEAR fixture/furniture mismatch (the
          floor-sink: facts say the sink is mounted at counter height but the render
          grounds it). Catches CONTEXT-specific errors a generic judge can't.
    This generalizes 'judge each stage's output' instead of hand-coding a Tier-1
    rule per failure mode (PD request). Strictness = ONLY-WHEN-OBVIOUS: fail-open
    (True) on uncertainty / stylistic / error / no API. Returns True = keep."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        import tempfile as _tf
        dur = 5.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4_path)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 5.0)
        except Exception:
            pass
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        parts = []
        with _tf.TemporaryDirectory() as td:
            for i in range(n_frames):
                t = dur * (i + 0.5) / n_frames
                fp = Path(td) / f"s{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path),
                                "-frames:v", "1", str(fp)], check=False, timeout=20)
                if fp.exists():
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return True
            n_render = len(parts)
            have_ref = False
            try:
                if scene_ref_path and Path(scene_ref_path).exists():
                    parts.append(_gt.Part.from_bytes(
                        data=Path(scene_ref_path).read_bytes(), mime_type="image/jpeg"))
                    have_ref = True
            except Exception:
                have_ref = False
            ref_clause = (
                f" The LAST image is the REFERENCE PHOTO of how this room is supposed "
                f"to look; the first {n_render} image(s) are the rendered cut."
                if have_ref else "")
            facts_clause = (f" Authoritative facts about this room/set (ground truth): "
                            f"{expected_facts}" if expected_facts else "")
            prompt = (
                "You check ONLY the SCENE/SET of a rendered pet video cut — ignore the "
                "pets' markings (another checker handles that). Look CAREFULLY." +
                ref_clause + facts_clause +
                " Decide TWO things: (1) obvious_defect = is there an OBVIOUS, "
                "unmistakable real-world-impossible defect a real photo would never "
                "have? (an object melted/warped/dissolving; an item floating with no "
                "support; the SAME furniture duplicated; an impossible/extra/merged "
                "limb). (2) intent_mismatch = compared to the reference/facts above, is "
                "a MAJOR fixture or furniture CLEARLY in the wrong place or wrong form? "
                "The key example: a sink/basin/washbasin that the facts say is MOUNTED "
                "at counter height but in the render is sitting DOWN ON THE FLOOR — that "
                "is intent_mismatch=true. Also: a fixture missing or relocated to an "
                "impossible spot. For BOTH: ignore minor differences in lighting, angle, "
                "camera framing, pet pose, small decor — and when genuinely in doubt, "
                "answer FALSE (we only act on clear problems). Return ONLY JSON: "
                "{\"obvious_defect\":true|false,\"intent_mismatch\":true|false,"
                "\"note\":\"3-6 word reason if either true, else empty\"}.")
            contents = list(parts) + [prompt]
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=contents,
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            import json as _json
            d = _json.loads((resp.text or "{}").strip())
            defect = bool(d.get("obvious_defect"))
            mismatch = bool(d.get("intent_mismatch")) if (have_ref or expected_facts) else False
            bad = defect or mismatch
            if bad:
                why = "scene-defect" if defect else "intent-mismatch"
                log.info("cut scene gate: %s[%s] in %s", why, d.get("note", ""),
                         Path(mp4_path).name)
            return not bad
    except Exception as e:
        log.warning("cut scene check failed (%s) — keeping cut", e)
        return True


def _who_and_emph(prompt: str) -> tuple[str, str]:
    """Infer which pets a motion prompt is about + the matching marking-canon
    emphasis to append on a regen. Shared by the i2v and ref gate sites so both
    behave identically."""
    pl = (prompt or "").lower()
    has_r = any(k in pl for k in ("ryani", "랴니", "french bulldog"))
    has_l = any(k in pl for k in ("leo", "레오", "tabby", "cat", "고양이"))
    who = "both" if (has_r and has_l) else ("ryani" if has_r else ("leo" if has_l else ""))
    emph = (_RYANI_MARKING_EMPHASIS if has_r else "") + (_LEO_MARKING_EMPHASIS if has_l else "")
    return who, emph


def _set_expected_facts(set_anchor: str) -> str:
    """Load the authoritative PD facts for a set (set_library[anchor].pd_notes) as a
    single string for the intent-match gate — the floor-sink case needs the fact
    'sink mounted at counter height' to be catchable. Empty string if none."""
    if not set_anchor:
        return ""
    try:
        lib_path = ROOT / "data" / "set_library.json"
        if not lib_path.exists():
            return ""
        data = json.loads(lib_path.read_text(encoding="utf-8"))
        notes = (data.get(set_anchor) or {}).get("pd_notes") or []
        if isinstance(notes, str):
            notes = [notes]
        return " ".join(str(n) for n in notes)[:1200]
    except Exception:
        return ""


def _gate_and_heal(out_mp4, prompt, who, emph, regen, progress_cb, dry_run,
                   manifests, tag, scene_ref_path=None, expected_facts: str = "") -> bool:
    """PD 2026-06-08 per-cut self-heal, shared by i2v + ref dispatch. After a cut
    renders, run the angle-aware render gate (character markings + scene coherence +
    intent-vs-reference); on failure regenerate via the `regen(prompt_text)` callable
    ×3 with strengthened canon → 1 alt prompt → drop the cut (flag for PD-confirmed
    story rework). `scene_ref_path`/`expected_facts` enable the intent-match check.
    Returns True if the cut is kept/clean, False if dropped."""
    def _ok():
        # Two focused calls (character + scene) — bundling dilutes attention.
        if not _cut_character_ok(out_mp4, who):
            return False
        return _cut_scene_ok(out_mp4, scene_ref_path=scene_ref_path,
                             expected_facts=expected_facts)
    if not who or dry_run or not out_mp4.exists():
        return True
    if _ok():
        return True
    resolved = False
    for r in range(3):
        if progress_cb:
            progress_cb(f":repeat: {tag} 캐릭터/장면 이상({who}) — 재생성 {r+1}/3")
        try:
            regen(prompt + emph)
        except Exception as e:
            log.warning("regen %d failed for %s: %s", r + 1, tag, e); break
        if _ok():
            resolved = True; break
    if not resolved:
        if progress_cb:
            progress_cb(f":repeat: {tag} — 다른 프롬프트로 재도전")
        try:
            regen("Gentle natural motion, camera holds still." + emph)
            resolved = _ok()
        except Exception:
            pass
    if not resolved:
        log.warning("av cut %s: character unresolved — dropping cut", tag)
        if progress_cb:
            progress_cb(f":x: {tag} 캐릭터 해결 실패 — 컷 드롭 "
                        f"(스토리 영향 → PD 컨펌 후 재작업 필요)")
        try:
            out_mp4.unlink(missing_ok=True)
        except Exception:
            pass
        manifests.setdefault("_dropped_cuts", []).append(tag)
        return False
    return True


def _sanitize_motion_prompt(prompt: str) -> str:
    """PD 2026-06-08: rewrite a motion_prompt that tripped Ark text moderation
    (InputTextSensitiveContentDetected) into a safe one — strip proper nouns
    (Leo/Ryani/랴니/레오/랴니엄마) → breed/color descriptors, drop moderation-prone
    verbs (warp/morph/animate/transform), collapse to a calm generic motion."""
    import re as _re
    p = prompt or ""
    repl = {
        r"\b[Rr]yani\b": "the small black French Bulldog",
        r"\b[Ll]eo\b": "the orange tabby cat",
        "랴니엄마": "the dog", "랴니": "the dog", "레오": "the cat",
    }
    for pat, sub in repl.items():
        p = _re.sub(pat, sub, p)
    for risky in ("warp", "morph", "animate", "transform", "deform", "melt",
                  "explode", "blood", "weapon"):
        p = _re.sub(rf"\b{risky}\w*\b", "move", p, flags=_re.IGNORECASE)
    p = p.strip()
    if len(p) < 20:
        p = ("A small black French Bulldog and an orange tabby cat in a cozy "
             "home. They move gently and naturally. Camera holds still.")
    return p


def _run_i2v_pipeline(manifests: dict, card: dict, work_dir: Path,
                      progress_cb: ProgressCb = None,
                      dry_run: bool = False) -> Path:
    """Image-to-video pipeline (original): GPT image gen → Veo i2v."""
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    sources = json.loads(Path(manifests["sources"]).read_text(encoding="utf-8"))
    cuts = manifests["cuts"]
    payload = json.loads(card.get("payload_json", "{}"))

    input_dir = work_dir / "input"
    regen_dir = work_dir / "regen"
    # Per-render anim_dir (was global). This lets the cross-attempt cache work
    # correctly — only THIS render's mp4s are visible, not leftovers from
    # past episodes.
    anim_dir = work_dir / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: preprocess photos for i2v
    _run(
        ["python3", "scripts/preprocess_for_i2v.py",
         "--manifest", manifests["sources"],
         "--out-dir", str(input_dir)],
        ":gear: [1/6] Preprocessing photos",
        progress_cb, dry_run,
    )

    # Step 2: AI regen via GPT character generation
    if progress_cb:
        progress_cb(":art: [2/6] AI 캐릭터 생성 시작...")
    if not dry_run:
        from scripts.generate_character_scene import generate_batch
        failures = generate_batch(
            Path(manifests["regen_prompts"]),
            input_dir if input_dir.exists() else None,
            regen_dir,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            progress_cb=progress_cb,
        )
        if failures:
            total = len([k for k in json.loads(Path(manifests["regen_prompts"]).read_text()).keys() if not k.startswith("_")])
            success = total - failures
            min_required = max(4, int(total * 0.75))
            if success < min_required:
                raise RuntimeError(f"AI 캐릭터 생성 {failures}/{total}건 실패 (최소 {min_required}컷 필요)")
            log.warning("AI 캐릭터 생성 %d/%d 실패 — 성공한 %d컷으로 진행", failures, total, success)
    else:
        log.info("[dry-run] would generate character scenes")

    # Step 3: Seedance 2.0 per cut, dispatched on seedance_mode (or Veo fallback)
    hero_motions = payload.get("hero_motion") or []
    motion_map = {hm["asset_id"]: hm.get("motion_prompt", "") for hm in hero_motions}
    use_seedance = bool(os.environ.get("BYTEPLUS_API_KEY", ""))
    i2v_engine = "Seedance" if use_seedance else "Veo"
    concept_cuts = manifests.get("concept_cuts", [])

    # Set anchor: Director writes one detailed set_description at the concept
    # level. We prepend it verbatim to every cut's motion_prompt so the LLM
    # can't drift on window position / sofa color / wallpaper / furniture
    # placement cut-to-cut. Empty string if Director didn't supply.
    concept_obj = manifests.get("concept", {}) or {}
    set_description = (concept_obj.get("set_description") or "").strip()
    if not set_description:
        # Fall back to any cut[0].space (legacy)
        if concept_cuts and concept_cuts[0].get("space"):
            set_description = f"Scene takes place in {concept_cuts[0]['space']}."
    if set_description and progress_cb:
        progress_cb(f":house: set anchor: {set_description[:80]}{'...' if len(set_description) > 80 else ''}")

    # Scene ref: give Seedance an empty-room reference image so it has a
    # concrete visual anchor for window position / sofa color / etc.
    # Resolution: (1) canonical library `assets/scene_refs/<set_anchor>.png`
    # shared across all episodes that use the same set, (2) per-render
    # GPT-generated fallback for unknown / special-concept sets.
    scene_ref_path: Path | None = None
    if use_seedance:
        set_anchor = concept_obj.get("set_anchor")
        fallback_path = work_dir / "scene_ref.png"
        if progress_cb and set_anchor:
            progress_cb(f":frame_with_picture: scene_ref resolving (set_anchor={set_anchor})")
        scene_ref_path = _resolve_scene_ref(
            set_anchor, set_description, fallback_path, dry_run=dry_run,
        )
        if scene_ref_path and progress_cb:
            origin = "library" if "scene_refs" in str(scene_ref_path) else "fallback (GPT)"
            progress_cb(f":white_check_mark: scene_ref ready ({origin}) → {scene_ref_path.name}")

    for i, item in enumerate(cuts):
        tag = item["tag"]
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        mode = cc.get("seedance_mode", "i2v")
        out_mp4 = anim_dir / f"{tag}.mp4"
        asset_id = item["asset"].get("asset_id", "")

        # Skip if this cut was already rendered in an earlier retry attempt
        # within this same work_dir. anim_dir is per-render (work_dir/animated).
        # Cache invalidation: if retry_loop.inject_giri_feedback added a
        # "CRITICAL FIX:" marker to this cut's motion_prompt, the prompt has
        # changed since last attempt — must re-render. Other cuts (unchanged
        # by the fix) reuse the cached output. Saves ~$0.05 + ~3min per cut.
        critical_fix = "CRITICAL FIX:" in (cc.get("motion_prompt") or "") \
                       or "CRITICAL FIX:" in (cc.get("regen_prompt") or "")
        if out_mp4.exists() and out_mp4.stat().st_size > 10_000 and not critical_fix:
            if progress_cb:
                progress_cb(f":fast_forward: [3/6] {tag} cached — skip Seedance")
            continue
        if critical_fix and out_mp4.exists():
            # Force fresh render — clear stale output
            out_mp4.unlink(missing_ok=True)

        # Pick prompt: motion_prompt (Director) takes priority over hero_motion legacy
        cut_prompt = (
            cc.get("motion_prompt")
            or motion_map.get(asset_id)
            or "gentle natural motion, slow blink, slight head movement, soft breathing"
        )
        # Prepend the verbatim set_description for cross-cut visual consistency.
        # Director's per-cut motion_prompt now focuses on character action; the
        # background / furniture / window / wallpaper details come from the
        # set anchor, identical for every cut in this concept.
        if set_description and not cut_prompt.startswith(set_description[:40]):
            prompt = f"{set_description} {cut_prompt}"
        else:
            prompt = cut_prompt

        # Auto-inject anti-stereotype guardrails for this set (PD-verified
        # 2026-06-01: explicit "NO tile walls" / "Korean detached-house" /
        # "white painted plain walls" prevents Seedance hallucinations).
        try:
            sa_for_anti = (
                cc.get("set_anchor")
                or (manifests.get("concept") or {}).get("set_anchor")
                or (cc.get("background_plan") or {}).get("set_anchor")
                or ((manifests.get("concept") or {}).get("background_plan") or {}).get("set_anchor")
            )
            if sa_for_anti:
                lib_path = ROOT / "data" / "set_library.json"
                if lib_path.exists():
                    lib_data = json.loads(lib_path.read_text(encoding="utf-8"))
                    anti = (lib_data.get(sa_for_anti) or {}).get("anti_stereotype_phrase")
                    if anti and anti.strip() and anti.strip() not in prompt:
                        prompt = prompt + " " + anti.strip()
        except Exception:
            pass

        # Conditional clothing rule (PD 2026-06-01 PM): default bare-furred,
        # EXCEPT for set_anchors with `requires_harness: true` (cafe, outdoor,
        # vet, etc.) — there pets MUST wear harnesses for realism.
        requires_harness = False
        try:
            if sa_for_anti and lib_data:
                requires_harness = bool(
                    (lib_data.get(sa_for_anti) or {}).get("requires_harness")
                )
        except Exception:
            pass
        if requires_harness:
            harness = (
                "Ryani wears a soft dark-grey nylon chest harness (no straps "
                "around the neck — chest-style, no buckles visible from this "
                "angle, no text or logo on the harness fabric). Leo wears a "
                "slim red nylon chest harness (same chest-style, no text or "
                "logo). NO leashes visible in frame. NO collars. NO bandanas. "
                "Just the chest harnesses, plain solid colors."
            )
            if harness not in prompt:
                prompt = prompt + " " + harness
            # PD 2026-06-02: at cafes specifically, a small folded white hand
            # towel goes on the chair seat BEFORE either pet sits down. Pets
            # sit ON the towel, never directly on the wooden chair.
            is_cafe = isinstance(sa_for_anti, str) and sa_for_anti.startswith("cafe_")
            if is_cafe:
                towel = (
                    "Whenever either pet sits on a cafe chair, a small folded "
                    "white cotton hand towel is placed on the chair seat "
                    "first; the pet sits ON the white towel, not directly on "
                    "the wood. The folded towel is clearly visible under the "
                    "pet's body."
                )
                if towel not in prompt:
                    prompt = prompt + " " + towel
        else:
            no_clothing = (
                "Ryani and Leo are completely bare-furred — NO clothing, NO hanbok, "
                "NO towels wrapped around them, NO bathrobes, NO pajamas, NO "
                "collars, NO bandanas, NO costumes of any kind. Their natural fur "
                "is the only thing covering their bodies."
            )
            if no_clothing not in prompt:
                prompt = prompt + " " + no_clothing

        # Background stillness guardrail (PD 2026-06-01 PM: "갑자기 화분이 움직
        # 이고"). Seedance freelances animation on bg objects — pots, books,
        # plants spontaneously move. Lock everything except the pets.
        bg_still = (
            "Background objects (plants in pots, books, decor, picture frames, "
            "furniture, lamps, dishes) are completely static throughout — "
            "ONLY the named pets and explicitly mentioned hands move. No "
            "shaking, drifting, swaying, or floating of stationary objects."
        )
        if bg_still not in prompt:
            prompt = prompt + " " + bg_still

        # No-text-on-packaging guardrail (PD 2026-06-01 PM: "사료에 글자는
        # 없어도 돼"). Seedance hallucinates Korean/English text and logos
        # onto kibble bags, food bowls, signs, walls. Blanket suppression.
        no_text = (
            "NO text, NO letters, NO words, NO logos, NO brand markings, "
            "NO writing anywhere in the frame — kibble bags are plain, food "
            "bowls are plain, packaging is plain, no readable signage."
        )
        if no_text not in prompt:
            prompt = prompt + " " + no_text

        # Spatial anchor lock (PD 2026-06-02: "컷과 컷 사이에 배경이나 오브
        # 젝트 위치 차이가 너무 크지 않도록 cameraman이 보정해야해"). Pull
        # persistent_background layout from set_library and append as a
        # FIXED-positions directive on every cut. Text reinforcement of
        # the spatial layout reduces Seedance's tendency to relocate
        # furniture across chain cuts.
        try:
            if sa_for_anti and lib_data:
                set_entry = lib_data.get(sa_for_anti) or {}
                pb = set_entry.get("persistent_background") or {}
                anchor_lines = []
                if isinstance(pb, dict):
                    for k in ("main_furniture", "wall", "floor", "window",
                              "light", "recurring_items"):
                        v = pb.get(k)
                        if isinstance(v, str) and v.strip():
                            anchor_lines.append(f"{k}: {v.strip()}")
                        elif isinstance(v, list):
                            joined = ", ".join(
                                str(item).strip() for item in v if item
                            )
                            if joined:
                                anchor_lines.append(f"{k}: {joined}")
                if anchor_lines:
                    spatial_lock = (
                        "SPATIAL ANCHOR LOCK (every cut MUST match — do "
                        "not relocate or resize): "
                        + "; ".join(anchor_lines) + "."
                    )
                    if "SPATIAL ANCHOR LOCK" not in prompt:
                        prompt = prompt + " " + spatial_lock
                else:
                    generic_lock = (
                        "Background object positions (furniture, decor, "
                        "scratcher, piano, console, plants) are LOCKED at "
                        "their established positions across every cut — do "
                        "NOT relocate, swap walls, or resize them."
                    )
                    if generic_lock not in prompt:
                        prompt = prompt + " " + generic_lock
        except Exception as ex:
            log.debug("spatial-lock injection skipped: %s", ex)

        # Per-cut character marking enforcement (PD 2026-06-02). Chain cuts
        # use i2v with prev frame as anchor — if cut N-1 drifted the marking,
        # the drift cascades. Re-state the canonical descriptions in EVERY
        # cut that mentions the pets so text re-anchors Seedance each call.
        prompt_lower = prompt.lower()
        if ("ryani" in prompt_lower or "랴니" in prompt_lower or
                "french bulldog" in prompt_lower):
            ryani_canon = (
                "Ryani is unambiguously a SPAYED FEMALE dog (she/her, "
                "11-year-old senior female French Bulldog — channel's 랴니"
                "엄마 / Mom-Ryani). Her underbelly between her hind legs is "
                "completely SMOOTH and BARE — NO penis, NO sheath, NO "
                "testicles, NO scrotum, NO male anatomy of any kind, NOT "
                "intact, NOT a male dog. Anatomically clearly female. Her "
                "rear from belly to hind legs is clean smooth black fur "
                "with NO visible genitalia at all (she is spayed). "
                "Ryani's markings (CONSISTENT EVERY CUT): THIN Boston "
                "Terrier-style white blaze (a narrow line, NOT the typical "
                "wide splash) from nose to forehead, white dot above each "
                "eye, silver-grey aged muzzle, white chin, large white "
                "chest patch, bat ears, ABSOLUTELY NO TAIL, stocky compact "
                "feminine body (petite, refined, NOT muscular barrel-chested "
                "male), only black/white/grey — no brown."
            )
            if "Ryani's markings (CONSISTENT EVERY CUT)" not in prompt:
                prompt = prompt + " " + ryani_canon
        if ("leo" in prompt_lower or "레오" in prompt_lower or
                "orange tabby" in prompt_lower):
            leo_canon = (
                "Leo's markings (CONSISTENT EVERY CUT): Leo is MALE (he/him, "
                "young 8-month-old male orange tabby — channel's 아들 레오 / "
                "Son-Leo). Pale yellow-green chartreuse eyes, white chin "
                "tuft, lean and agile body, paler cream-orange cheeks and "
                "belly than the back."
            )
            if "Leo's markings (CONSISTENT EVERY CUT)" not in prompt:
                prompt = prompt + " " + leo_canon

        # Safety filter auto-replace (2026-05-31, PD insight): Seedance's
        # "InputImageSensitiveContentDetected.PrivacyInformation" errors are
        # triggered by SUGGESTIVE PROMPT TEXT, not by humans in reference
        # images. Veo had similar issue. We pre-scrub before dispatch.
        SAFETY_REPLACEMENTS = [
            ("belly fully exposed", "belly visible"),
            ("belly exposed", "belly visible"),
            ("belly upward", "belly facing up"),
            ("belly up", "belly facing up"),
            ("hind quarters lifted high", "hind quarters raised in play bow stance"),
            ("hind quarters lift even higher", "deepens her play bow stance"),
            ("hind quarters lift up high", "hind quarters raised in play bow stance"),
            ("hindquarters raised high", "hind quarters raised in play bow stance"),
            ("hindquarters lift", "hind quarters raised in play bow stance"),
            ("rear end raised high", "hind quarters in play bow stance"),
            ("rear end raised", "hind quarters in play bow stance"),
            ("rear end", "hind quarters"),
            ("spread legs", "legs apart naturally"),
            ("legs spread", "legs apart naturally"),
            ("paws lifting toward the ceiling", "paws lifted softly in the air"),
            ("paws lifted softly into the air", "paws relaxed in the air"),
            ("sprawled", "lying comfortably"),
            ("rises and falls", "breathes gently"),
            ("his cream-orange belly fully exposed", "his cream-orange belly visible"),
            ("belly upward, front paws lifting toward the ceiling", "belly facing up, front paws relaxed"),
        ]
        original_prompt = prompt
        for bad, good in SAFETY_REPLACEMENTS:
            if bad.lower() in prompt.lower():
                # Case-insensitive replace, preserving rest of casing
                import re as _re
                prompt = _re.sub(_re.escape(bad), good, prompt, flags=_re.IGNORECASE)
        if prompt != original_prompt:
            log.info("safety filter: scrubbed prompt for %s", tag)

        if not use_seedance:
            # Veo fallback (no Seedance API key) — i2v from regen still only
            regen_png = regen_dir / f"{tag}.png"
            cmd = [
                "python3", "scripts/animate_hero_veo3_vertex.py",
                "--image", str(regen_png),
                "--prompt", prompt,
                "--seconds", "4",
                "--model", os.getenv("VEO_MODEL", "veo-3.0-generate-001"),
                "--output", str(out_mp4),
            ]
            _run(cmd, f":film_frames: [3/6] Veo i2v {tag}", progress_cb, dry_run)
            continue

        seconds_int = int(cc.get("duration_seconds", 5))
        # Fast model + ref mode = 5s hard cap. Clamp to avoid Ark API error.
        model_in_use = os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE)
        if "fast" in model_in_use and mode == "ref" and seconds_int > FAST_MODEL_REF_MAX_SECONDS:
            log.info("clamping %s duration %ds → %ds (fast model + ref cap)",
                     tag, seconds_int, FAST_MODEL_REF_MAX_SECONDS)
            seconds_int = FAST_MODEL_REF_MAX_SECONDS
        seconds = str(seconds_int)

        if mode == "ref":
            ref_names = cc.get("references") or ["pair"]
            ref_paths = [_resolve_ref(n) for n in ref_names]
            ref_paths = [p for p in ref_paths if p is not None]
            if not ref_paths:
                log.warning("ref mode %s: no resolved refs, falling back to i2v", tag)
                mode = "i2v"
            else:
                # Add scene_ref as ADDITIONAL anchor (BytePlus allows up to 9 refs).
                # Character refs anchor identity; scene ref anchors the room.
                full_refs = list(ref_paths)
                if scene_ref_path and scene_ref_path.exists():
                    full_refs.append(scene_ref_path)
                # Omni reference (2026-05-31, PD request): pull `scene_ref_extras`
                # from set_library — extra PD-real-photos of the same room from
                # different POVs. Seedance learns the room from multi-photo
                # evidence instead of one scene_ref + text description.
                extras_added = 0
                try:
                    concept_obj = manifests.get("concept") or {}
                    set_anchor = (
                        cc.get("set_anchor")
                        or concept_obj.get("set_anchor")
                        or (cc.get("background_plan") or {}).get("set_anchor")
                        or (concept_obj.get("background_plan") or {}).get("set_anchor")
                        or (concept_cuts[0].get("set_anchor") if concept_cuts else None)
                    )
                    if set_anchor:
                        lib_path = ROOT / "data" / "set_library.json"
                        if lib_path.exists():
                            lib_data = json.loads(lib_path.read_text(encoding="utf-8"))
                            extras = (lib_data.get(set_anchor) or {}).get("scene_ref_extras") or []
                            for e in extras:
                                ep = ROOT / e if not Path(e).is_absolute() else Path(e)
                                if ep.exists() and ep not in full_refs:
                                    full_refs.append(ep)
                                    extras_added += 1
                                    if len(full_refs) >= 9:
                                        break
                            if extras_added:
                                log.info("omni refs added for %s: %d photos", set_anchor, extras_added)
                        else:
                            log.warning("set_library.json missing for omni ref")
                    else:
                        log.warning("omni ref: no set_anchor found in cut/concept/cuts[0]")
                except Exception as ex:
                    log.warning("omni ref load failed: %s", ex)
                full_refs = full_refs[:9]

                # Resolve optional reference_video URL once (set_library per anchor)
                ref_video_url = None
                ref_video_lbl = ""
                try:
                    if sa_for_anti:
                        rv_url = (lib_data.get(sa_for_anti) or {}).get("reference_video_url")
                        if rv_url:
                            ref_video_url = rv_url
                            ref_video_lbl = " +R2V"
                except Exception:
                    pass
                scene_lbl = " +scene" if scene_ref_path and scene_ref_path in full_refs else ""
                omni_lbl = f" +omni×{extras_added}" if extras_added else ""

                def _seedance_ref(p: str):
                    rcmd = [
                        "python3", "scripts/animate_seedance_i2v.py",
                        "--mode", "ref",
                        "--prompt", p,
                        "--seconds", seconds,
                        "--model", os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE),
                        "--output", str(out_mp4),
                    ]
                    for rp in full_refs:
                        rcmd.extend(["--ref-image", str(rp)])
                    if ref_video_url:
                        rcmd.extend(["--ref-video", ref_video_url])
                    _run(rcmd, f":film_frames: [3/6] Seedance ref {tag} "
                         f"({len(full_refs)} refs{scene_lbl}{omni_lbl}{ref_video_lbl})",
                         progress_cb, dry_run)

                def _seedance_ref_safe(p: str):
                    try:
                        _seedance_ref(p)
                    except RuntimeError as e:
                        if "SensitiveContent" in str(e) or "BadRequest" in str(e):
                            log.warning("Seedance moderation on %s (ref) — sanitized retry", tag)
                            if progress_cb:
                                progress_cb(f":shield: {tag} 모더레이션 — 정제 프롬프트 재시도")
                            _seedance_ref(_sanitize_motion_prompt(p))
                        else:
                            raise

                _seedance_ref_safe(prompt)
                # PD 2026-06-08: ref-mode cuts were skipping the per-cut character
                # gate entirely (it lived only in the i2v branch). Apply the SAME
                # angle-aware Ryani/Leo gate + self-heal (regen ×3 → alt → drop).
                _who, _emph = _who_and_emph(prompt)
                _gate_and_heal(out_mp4, prompt, _who, _emph, _seedance_ref_safe,
                               progress_cb, dry_run, manifests, tag,
                               scene_ref_path=scene_ref_path,
                               expected_facts=_set_expected_facts(set_anchor))
                continue

        if mode == "interp":
            # Rare path for ai_vtuber: explicit interp between two stills.
            # If Director didn't supply both stills, fall back to i2v.
            anchors = cc.get("fill_anchors") or {}
            first_p = anchors.get("first_frame_path")
            last_p = anchors.get("last_frame_path")
            if first_p and last_p and Path(first_p).exists() and Path(last_p).exists():
                cmd = [
                    "python3", "scripts/animate_seedance_i2v.py",
                    "--mode", "interp",
                    "--image", first_p,
                    "--last-frame", last_p,
                    "--prompt", prompt,
                    "--seconds", seconds,
                    "--model", os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE),
                    "--output", str(out_mp4),
                ]
                _run(cmd, f":film_frames: [3/6] Seedance interp {tag}",
                     progress_cb, dry_run)
                continue
            else:
                log.warning("interp mode %s: anchors missing, falling back to i2v", tag)
                mode = "i2v"

        # mode == "i2v" (default and fallback)
        # Director may specify `first_frame_asset_id` pointing at a REAL photo
        # from recommended_assets. Used to ground cut1 (and any cut) in a real
        # snapshot rather than a GPT-generated still — dramatically reduces
        # the AI-look. Added 2026-05-31 at PD request.
        first_frame_path = None
        ff_asset_id = cc.get("first_frame_asset_id")
        if ff_asset_id:
            try:
                import sqlite3 as _sql
                _con = _sql.connect(ROOT / "data" / "agent.db")
                row = _con.execute(
                    "SELECT file_path FROM assets WHERE asset_id=?", (ff_asset_id,)
                ).fetchone()
                _con.close()
                if row and row[0]:
                    rp = Path(row[0])
                    if not rp.is_absolute():
                        rp = ROOT / rp
                    if rp.exists():
                        first_frame_path = rp
                        log.info("i2v %s: using REAL PHOTO %s as first_frame (anti-AI)",
                                 tag, ff_asset_id)
            except Exception as e:
                log.warning("first_frame_asset_id resolve failed for %s: %s",
                            ff_asset_id, e)
        if first_frame_path is None:
            first_frame_path = regen_dir / f"{tag}.png"

        # Chain-mode override (PD 2026-06-01): if this cut is marked
        # chain_from_prev, extract the LAST frame of the previous cut's mp4
        # and use that as the i2v first_frame. Cascades bg/character
        # continuity through the cut chain. Cut 1 still uses regen still
        # or asset_id-based first_frame (set above).
        if cc.get("chain_from_prev") and i > 0:
            prev_tag = cuts[i - 1].get("tag")
            prev_mp4 = anim_dir / f"{prev_tag}.mp4"
            if prev_mp4.exists():
                chain_jpg = anim_dir / f"_chain_{tag}.jpg"
                _run([
                    "ffmpeg", "-y", "-sseof", "-0.5", "-i", str(prev_mp4),
                    "-frames:v", "1", "-q:v", "2", str(chain_jpg),
                ], f":link: [3/6] Chain {tag} ← last frame of {prev_tag}",
                    progress_cb, dry_run)
                if chain_jpg.exists():
                    first_frame_path = chain_jpg
                    # Light continuity hint (PD 2026-06-01): explicitly tell
                    # Seedance to preserve the lighting/shadow direction/
                    # color temperature from the input frame. Without this,
                    # i2v can drift the time-of-day across chain cuts.
                    light_hint = (
                        " Lighting, shadow direction, and color temperature "
                        "exactly match the input frame — same time-of-day, "
                        "same window-light position, identical warm/cool "
                        "balance. Do not change the lighting setup."
                    )
                    if light_hint.strip() not in prompt:
                        prompt = prompt + light_hint

        def _seedance_i2v(p: str, image=None):
            cmd = [
                "python3", "scripts/animate_seedance_i2v.py",
                "--mode", "i2v",
                "--image", str(image or first_frame_path),
                "--prompt", p,
                "--seconds", seconds,
                "--model", os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE),
                "--output", str(out_mp4),
            ]
            _run(cmd, f":film_frames: [3/6] Seedance i2v {tag}", progress_cb, dry_run)
        # PD 2026-06-08: Ark text moderation ("InputTextSensitiveContentDetected")
        # was failing a single cut → whole av episode retried (same prompt) → av
        # never produced. On that error, retry the cut with a sanitized prompt
        # (strip proper nouns / risky verbs per CLAUDE.md motion rules).
        def _seedance_i2v_safe(p: str, image=None):
            try:
                _seedance_i2v(p, image=image)
            except RuntimeError as e:
                if "SensitiveContent" in str(e) or "BadRequest" in str(e):
                    log.warning("Seedance moderation on %s — sanitized retry", tag)
                    if progress_cb:
                        progress_cb(f":shield: {tag} 모더레이션 — 정제 프롬프트 재시도")
                    _seedance_i2v(_sanitize_motion_prompt(p), image=image)
                else:
                    raise
        _seedance_i2v_safe(prompt)

        # PD 2026-06-08: per-cut CHARACTER gate (angle-aware) RIGHT AFTER render —
        # covers Ryani AND Leo (PD: "레오 생성형 티"). Bad (clear + wrong/generative)
        # → regenerate with strengthened canon ×3 → 1 alt prompt → drop. Dropping
        # breaks the chain/story → flag for PD-confirmed Writer/Producer rework (Phase 2).
        _who, _emph = _who_and_emph(prompt)
        # ROOT-CAUSE fix (PD 2026-06-08): a chain cut's i2v input is the PREVIOUS
        # cut's last frame, so marking drift (e.g. blaze widening) compounds down
        # the chain and a same-input regen can't fix it. On a chain cut, regenerate
        # from the FRESH canonical GPT still (thin-blaze ref) instead of the drifted
        # chain frame. Bind that into the regen callable for the shared self-heal.
        _fresh_still = regen_dir / f"{tag}.png"
        _regen_img = (_fresh_still if (cc.get("chain_from_prev")
                      and _fresh_still.exists()
                      and _fresh_still != first_frame_path) else None)

        def _i2v_regen(p: str):
            if _regen_img and progress_cb:
                progress_cb(f":arrows_counterclockwise: {tag} 체인 드리프트 → 원본 스틸로 재생성")
            _seedance_i2v_safe(p, image=_regen_img)

        _i2v_sa = cc.get("set_anchor") or set_anchor
        _gate_and_heal(out_mp4, prompt, _who, _emph, _i2v_regen,
                       progress_cb, dry_run, manifests, tag,
                       scene_ref_path=scene_ref_path,
                       expected_facts=_set_expected_facts(_i2v_sa))

    # Step 3a: per-cut fade-out + fade-in for smooth chain transitions
    # (PD 2026-06-01 PM: "컷사이 넘어갈때 f/o 통해서 어색함을 없애야해").
    # Body cuts: brief 0.3s fades (out at end, in at start). Last body cut
    # (right before wink): extended 1.5s lingering fade-out for emotional
    # weight ("마지막 씬은 항상 여운있게 천천히 f/o 좀 늘려줘"). Wink cut
    # has fade-in matching the lingering, no fade-out (let bumpers handle it).
    for i, item in enumerate(cuts):
        tag = item.get("tag")
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        src_mp4 = anim_dir / f"{tag}.mp4"
        if not src_mp4.exists():
            continue
        dur = float(cc.get("duration_seconds") or 5)
        is_wink = cc.get("function") == "wink_ending"
        # Last body cut = the cut immediately before a wink_ending cut
        next_cc = concept_cuts[i + 1] if i + 1 < len(concept_cuts) else {}
        is_last_body = (next_cc.get("function") == "wink_ending")
        # The genuinely final cut of the episode (wink, or last body if no wink)
        is_last_overall = (i == len(cuts) - 1)
        # PD 2026-06-08: av needs MORE 여운 — the actual last cut was ending abruptly
        # (wink had fade_out=0, no freeze). Mirror rf RF_END_FREEZE_S: freeze the final
        # frame for ~2s and fade out DURING that freeze. Applied below, after fades.
        end_freeze = float(os.getenv("AV_END_FREEZE_S", "2.0"))
        # Fade params
        if is_wink:
            fade_in_d, fade_out_d = 0.5, 0.0  # match lingering; out handled by freeze
        elif is_last_body:
            fade_in_d, fade_out_d = 0.3, (0.0 if is_last_overall else 1.5)
        else:
            fade_in_d, fade_out_d = (0.3 if i > 0 else 0.0), 0.3
        # Build filter expression
        filters = []
        if fade_in_d > 0:
            filters.append(f"fade=t=in:st=0:d={fade_in_d}")
        if fade_out_d > 0:
            fade_out_st = max(0, dur - fade_out_d)
            filters.append(f"fade=t=out:st={fade_out_st}:d={fade_out_d}")
        # Final cut 여운: clone-freeze last frame, then fade out across the freeze tail.
        if is_last_overall and end_freeze > 0.1:
            filters.append(f"tpad=stop_mode=clone:stop_duration={end_freeze:.2f}")
            fade_d = min(end_freeze, max(0.6, end_freeze - 0.2))
            fade_st = max(0.0, dur + end_freeze - fade_d)
            filters.append(f"fade=t=out:st={fade_st:.2f}:d={fade_d:.2f}")
        if not filters:
            continue
        faded_mp4 = anim_dir / f"{tag}_faded.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-filter:v", ",".join(filters),
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "18",
            str(faded_mp4),
        ]
        label = "wink-in" if is_wink else ("last-body" if is_last_body else "body")
        _run(cmd,
             f":curved_arrow: [3a/6] Fade {tag} ({label}: in={fade_in_d}s out={fade_out_d}s)",
             progress_cb, dry_run)
        if not dry_run and faded_mp4.exists():
            src_mp4.unlink()
            faded_mp4.rename(src_mp4)

    # Step 3b: ffmpeg slowdown for one-take short cuts (PD 2026-06-01).
    # Seedance fast-ref tops out at 5s but the playback feels rushed for a
    # one-take gag. Slow each cut to its target_duration_seconds via
    # setpts=<ratio>*PTS — captions were already authored against the
    # target playback length. cc metadata lives in concept_cuts (manifests
    # cuts only carry tag + asset).
    for i, item in enumerate(cuts):
        tag = item.get("tag")
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        tgt = int(cc.get("target_duration_seconds") or 0)
        src = int(cc.get("duration_seconds") or 0)
        # Last cut already carries an end-freeze tail (Step 3a 여운) — don't re-stretch it.
        if i == len(cuts) - 1:
            continue
        if tgt <= 0 or src <= 0 or tgt <= src or not tag:
            continue
        src_mp4 = anim_dir / f"{tag}.mp4"
        if not src_mp4.exists():
            continue
        ratio = tgt / src
        slowed_mp4 = anim_dir / f"{tag}_slow.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-filter:v", f"setpts={ratio:.3f}*PTS",
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "18",
            str(slowed_mp4),
        ]
        _run(cmd, f":hourglass: [3b/6] Slow {tag} {src}s→{tgt}s ({ratio:.2f}x)",
             progress_cb, dry_run)
        if not dry_run and slowed_mp4.exists():
            src_mp4.unlink()
            slowed_mp4.rename(src_mp4)

    # Step 4: build bumpers if needed
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            ["python3", "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            ":loud_sound: [4/6] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(":loud_sound: [4/6] Bumpers exist — skip")

    # PD 2026-06-08: PERSIST the expensive Seedance render BEFORE the fragile
    # finishing steps (VLM caption / burn / assemble). If anything downstream
    # fails or hangs, the billed Seedance cuts are saved here so we can re-caption
    # without re-paying Seedance. (Answers "seedance 결과 남겨두고 캡션부터 fix".)
    if not dry_run:
        try:
            raw_dir = ROOT / "data" / "output" / "seedance_raw" / f"{ts}_{card.get('card_id','')[:8]}"
            raw_dir.mkdir(parents=True, exist_ok=True)
            for mp4 in sorted(anim_dir.glob("*.mp4")):
                shutil.copy2(mp4, raw_dir / mp4.name)
            (raw_dir / "manifests.json").write_text(
                json.dumps({"sources": manifests.get("sources"),
                            "captions": manifests.get("captions"),
                            "card_id": card.get("card_id")}, ensure_ascii=False),
                encoding="utf-8")
            log.info("Seedance raw cuts archived → %s", raw_dir)
            if progress_cb:
                progress_cb(f":floppy_disk: Seedance 컷 보존 → seedance_raw/{raw_dir.name}")
        except Exception as ex:
            log.warning("Seedance raw archive failed (non-fatal): %s", ex)

    # Step 4b (PD 2026-06-02): VLM post-render check → caption rewrite.
    # Gemini Flash analyzes each animated cut's actual content, then the
    # Caption Agent (3-way competition + Opus judge) re-writes captions to
    # match what's truly on screen. Silent fallback if anything fails.
    try:
        _vlm_post_render_caption_rewrite(
            work_dir, manifests, cuts, concept_cuts, anim_dir,
            progress_cb=progress_cb, dry_run=dry_run,
        )
    except Exception as ex:
        log.warning("VLM caption rewrite failed (keeping original captions): %s", ex)

    # Step 5: burn captions (손글씨 기본, Director font_override 가능)
    _run(
        _burn_captions_cmd(manifests, anim_dir, ROOT / "data" / "output" / "animated_captioned"),
        ":speech_balloon: [5/6] Burning captions",
        progress_cb, dry_run,
    )

    # Step 6: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_av_{ts}.mp4"
    _run(
        ["python3", "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--intro-bumper", str(INTRO_BUMPER),
         "--outro-bumper", str(OUTRO_BUMPER),
         "--music", manifests.get("bgm", str(DEFAULT_BGM)),
         "--out", str(out)],
        ":clapper: [6/6] Final assembly",
        progress_cb, dry_run,
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Main entry: render_card
# ──────────────────────────────────────────────────────────────────────
def _prune_tmp_workdirs(keep: int | None = None) -> None:
    """PD 2026-06-06: delete old cameraman_* tmp workdirs (trimmed clips,
    photo_i2v, animated intermediates) — they accumulated to 16GB. Keep the
    most recent `keep` for debugging. Final episodes live in data/output/
    episodes and are never touched. Override count with CAMERAMAN_TMP_KEEP."""
    if keep is None:
        keep = int(os.getenv("CAMERAMAN_TMP_KEEP", "6"))
    try:
        tmp = ROOT / "data" / "tmp"
        dirs = sorted(
            [d for d in tmp.glob("cameraman_*") if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        removed = 0
        for d in dirs[keep:]:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        if removed:
            log.info("pruned %d old tmp workdirs (kept %d)", removed, keep)
    except Exception as e:
        log.warning("tmp prune failed: %s", e)


def render_card(card_id_prefix: str, *,
                progress_cb: ProgressCb = None,
                dry_run: bool = False,
                use_brain: bool = True,
                concept: dict | None = None) -> Path:
    con = _db()
    card = _load_card(con, card_id_prefix)
    card_id = card["card_id"]
    assets = _load_card_assets(con, card_id)
    run_id = _log_run_start(con, card_id)

    try:
        style = determine_render_style(card, assets)
        log.info("Card %s → style=%s, %d assets", card_id[:8], style, len(assets))
        if progress_cb:
            progress_cb(f":movie_camera: Rendering `{card_id[:8]}` with **{style}** pipeline")

        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = ROOT / "data" / "tmp" / f"cameraman_{card_id[:8]}_{ts}"

        # Use Brain for intelligent asset curation + camera directing
        # when no assets are pre-linked, or always when use_brain=True
        if use_brain and not dry_run:
            try:
                from agents.cameraman_brain import plan_shots, shot_list_to_manifests, search_assets as brain_search
                if progress_cb:
                    progress_cb(":brain: Cameraman Brain planning shots...")
                shot_list = plan_shots(card_id_prefix, progress_cb=progress_cb)

                # Build assets lookup for manifest conversion
                all_asset_ids = [c["asset_id"] for c in shot_list.get("cuts", [])]
                assets_lookup = {}
                for row in con.execute(
                    f"SELECT * FROM assets WHERE asset_id IN ({','.join('?' * len(all_asset_ids))})",
                    all_asset_ids,
                ).fetchall():
                    assets_lookup[row["asset_id"]] = dict(row)

                manifests = shot_list_to_manifests(shot_list, card, assets_lookup, style, work_dir)
            except Exception as e:
                log.warning("Brain failed, falling back to basic manifests: %s", e)
                if progress_cb:
                    progress_cb(f":warning: Brain fallback: {str(e)[:100]}")
                manifests = generate_manifests(card, assets, style, work_dir, concept=concept)
        else:
            manifests = generate_manifests(card, assets, style, work_dir, concept=concept)

        # Re-check style against actual resolved assets (skip for text-to-video)
        if manifests.get("generation_mode") != "text_to_video":
            resolved_assets = manifests.get("cuts", [])
            if resolved_assets:
                resolved_kinds = [c.get("asset", {}).get("kind") for c in resolved_assets if c.get("asset")]
                resolved_kinds = [k for k in resolved_kinds if k]  # filter None
                if resolved_kinds:
                    style = determine_render_style(card, [{"kind": k} for k in resolved_kinds], concept=concept)
                    log.info("Final style after asset resolve: %s", style)

        if style == "real_footage":
            out = run_real_footage_pipeline(manifests, work_dir, progress_cb, dry_run)
        elif style == "cartoon_sticker":
            # Legacy — redirect to ai_vtuber
            log.info("cartoon_sticker redirected to ai_vtuber")
            out = run_ai_vtuber_pipeline(manifests, card, work_dir, progress_cb, dry_run)
        elif style == "ai_vtuber":
            out = run_ai_vtuber_pipeline(manifests, card, work_dir, progress_cb, dry_run)
        else:
            raise RuntimeError(f"Unknown style: {style}")

        if not dry_run:
            con.execute(
                "UPDATE cards SET state='rendered', output_video_path=?, updated_at=datetime('now') WHERE card_id=?",
                (str(out), card_id),
            )
            con.commit()
        _log_run_end(con, run_id, "ok", json.dumps({"video": str(out), "style": style}))

        if progress_cb:
            size_mb = out.stat().st_size / 1e6 if not dry_run and out.exists() else 0
            progress_cb(f":white_check_mark: Rendered `{card_id[:8]}` → `{out.name}` ({size_mb:.1f} MB)")

        # PD 2026-06-06: intermediate clips piled up (16GB / 335 workdirs). Prune
        # old tmp workdirs, keeping only the most recent few for debugging. The
        # final episode lives in data/output/episodes (untouched).
        if not dry_run:
            _prune_tmp_workdirs()

        # Giri review is handled by retry_loop.py — not here
        return out

    except Exception as e:
        _log_run_end(con, run_id, "error", error=str(e)[:2000])
        if not dry_run:
            con.execute(
                "UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=? AND state='approved'",
                (card_id,),
            )
            con.commit()
        raise


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Cameraman Agent — render an approved card")
    p.add_argument("card_id", help="card_id prefix (first 8 chars is enough)")
    p.add_argument("--dry-run", action="store_true",
                   help="print pipeline steps without executing")
    p.add_argument("--no-brain", action="store_true",
                   help="skip VLM Brain, use basic manifest generation")
    args = p.parse_args()

    def _print_progress(msg: str) -> None:
        print(msg)

    try:
        out = render_card(args.card_id, progress_cb=_print_progress,
                          dry_run=args.dry_run, use_brain=not args.no_brain)
        print(f"\nDone: {out}")
        return 0
    except RuntimeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
