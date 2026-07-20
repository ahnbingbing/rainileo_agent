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

from agents import canon  # central character canon (single source of truth)

ROOT = Path(__file__).resolve().parent.parent

# PD 2026-06-10 COST GUARD: a hard ceiling on Seedance i2v calls per PROCESS. The
# self-heal test spent ~$100 in a day because retry layers COMPOUND — per-cut
# character/face gate re-renders × AV_MAX_RETRIES (whole-episode) × self-heal
# rounds → 246 Seedance cuts in one run. This counter is the absolute backstop:
# once a run exceeds SEEDANCE_MAX_CALLS it REFUSES further Seedance dispatches
# (raises) instead of silently burning money. A normal 4-episode batch is ~24
# cuts; 40 leaves headroom for modest retries but kills a runaway.
_SEEDANCE_CALL_COUNT = 0
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


def _recent_bgm_tracks(n: int = 16) -> list[str]:
    """The last `n` BGM tracks actually used, most-recent last. `bgm_by_video.json` is
    written at upload time (record_bgm_for_video) as an insertion-ordered dict video→track,
    so its last n values are the freshest picks. Used to AVOID replaying recent songs."""
    import json as _json
    try:
        d = _json.loads((ROOT / "data" / "bgm_by_video.json").read_text(encoding="utf-8"))
        return list(d.values())[-n:]
    except Exception:
        return []


def _pick_bgm_track(bgm_mood: str, seed_key: str) -> str:
    """Pick a BGM filename for a mood — biased HARD toward variety (PD 2026-07-01: "노래 좀
    다양하게"). Two repetition sources are fixed: (1) thin moods (some have only 1-2 tracks) →
    when the mood pool is small, widen to the FULL curated library so every episode has a deep
    pool; (2) no recency memory → EXCLUDE the last ~16 used tracks so consecutive episodes never
    replay the same song. Within the resulting fresh pool the choice is still hash(seed_key)-
    deterministic, so re-rendering one episode (before it uploads) stays stable."""
    import hashlib as _h
    import json as _json
    candidates = list(_BGM_MOOD_MAP.get(
        bgm_mood, ["backgroundmusicforvideos-cute-cheerful-whistle-cute-music-249653.mp3"]))
    all_tracks = sorted({t for v in _BGM_MOOD_MAP.values() for t in v})
    # PD 2026-06-24 (BGM 저작권 재발 방지): never pick a Content-ID-claimed track or one sharing
    # a claimed track's label (claims are often catalog-wide). Ledger from scripts/swap_bgm.py.
    try:
        _claimed = set(_json.loads((ROOT / "data" / "bgm_claimed.json").read_text(encoding="utf-8")))
    except Exception:
        _claimed = set()

    def _lbl(fn: str) -> str:
        t = fn.rsplit(".", 1)[0].split("-")
        return "lp-studio" if t[:2] == ["lp", "studio"] else (t[0] if t else fn)
    _bad = {_lbl(c) for c in _claimed}

    def _ok(fn: str) -> bool:
        return fn not in _claimed and _lbl(fn) not in _bad

    recent = set(_recent_bgm_tracks())
    mood_ok = [c for c in candidates if _ok(c)]
    # Prefer mood tracks not played recently; if the mood is thin/exhausted, widen to the whole
    # claim-safe library (still avoiding recent) so variety never collapses to one song.
    pool = [c for c in mood_ok if c not in recent]
    if len(pool) < 3:
        widened = [t for t in all_tracks if _ok(t) and t not in recent and t not in pool]
        pool = pool + widened
    pool = pool or mood_ok or [c for c in candidates if _ok(c)] or candidates
    seed = int(_h.sha1((seed_key or "default").encode("utf-8")).hexdigest()[:8], 16)
    return pool[seed % len(pool)]
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
    "ryani_young":      "assets/character_ref/ryani_young.png",  # PD 2026-06-11: 어린 랴니(2015, 회색 주둥이 없음) — 과거/회상 컷용
    # PD 2026-06-15: leo_solo.png was an AI ILLUSTRATION → made every AV Leo still
    # look cartoony (ref drives output STYLE). Repointed to a REAL photo crop.
    # ryani_solo / ryani_young are already real photos. See memory av_ref_must_be_real_photo.
    "leo_solo":         "assets/character_ref/leo_real.png",
    "leo_ai_OLD":       "assets/character_ref/leo_solo.png",  # retained, do not use for AV
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


# ── Fix 2 (PD 2026-06-28): PRECISE STILL for AV ───────────────────────────────
# Compose each locked-space cut's still from the CLEAN scene_ref (the literal room) +
# character refs placed per the cut — instead of letting Seedance freelance the room in
# ref mode (the Leo-teleport / background-junk class). The still IS the room, so i2v then
# animates a faithful frame and the background can't drift. Flag-gated: AV_PRECISE_STILL=1.
def _compose_av_still(scene_ref: Path, char_refs: list, prompt: str, out: Path) -> bool:
    try:
        from scripts.gen_still_multiref import generate as _gen
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            log.warning("AV_PRECISE_STILL: GOOGLE_API_KEY missing — skipping precise still")
            return False
        refs = [Path(r) for r in char_refs if r and Path(r).exists()]
        _gen(Path(scene_ref), refs, prompt, Path(out), key)
        return out.exists() and out.stat().st_size > 10_000
    except Exception as e:  # noqa: BLE001
        log.warning("precise still compose failed (%s): %s", out, e)
        return False


def _cut_char_refs(cc: dict) -> list:
    """Resolve the character-ref image paths for a cut — Director's `references` list first,
    else inferred from who the prompt is about."""
    paths = [p for p in (_resolve_ref(n) for n in (cc.get("references") or [])) if p]
    if not paths:
        who, _ = _who_and_emph((cc.get("motion_prompt") or "") + " " + (cc.get("action") or ""))
        if who == "both":
            paths = [p for p in (_resolve_ref("ryani_solo"), _resolve_ref("leo_solo")) if p]
        elif who:
            paths = [p for p in (_resolve_ref(f"{who}_solo"),) if p]
    return paths


def _av_still_compose_prompt(cc: dict) -> str:
    """Build the composition prompt for a precise still: anchor the room to the provided
    photo, place the pets per the cut, and be CAST-EXPLICIT — a single-subject cut must say
    the OTHER pet is NOT in the shot, so the model can't slip it into the background (the
    관찰왕 cut2 'Leo on a scratcher behind Ryani' class). Fix 3 (PD 2026-06-28)."""
    base = (cc.get("regen_prompt") or cc.get("action") or cc.get("motion_prompt") or "").strip()
    who, emph = _who_and_emph(base)
    # Cast directive: name exactly who is in frame; for a solo cut, exclude the other pet.
    if who == "ryani":
        cast = ("CAST: ONLY Ryani (the black no-tail French Bulldog) is in this shot. Leo (the "
                "orange tabby cat) is NOT present anywhere in this frame — not in the background, "
                "not on any bed/scratcher/sofa.")
    elif who == "leo":
        cast = ("CAST: ONLY Leo (the orange tabby cat) is in this shot. Ryani (the black French "
                "Bulldog) is NOT present anywhere in this frame.")
    elif who == "both":
        cast = ("CAST: BOTH pets — Ryani (black no-tail Frenchie) and Leo (orange tabby) — each "
                "appears EXACTLY ONCE. No third animal, no duplicate/clone of either.")
    else:
        cast = "Each named pet appears EXACTLY ONCE; no duplicate/clone of any animal."
    return (
        "Use the PROVIDED ROOM PHOTO as the EXACT background — keep it pixel-identical, do "
        "NOT redraw, relocate, or re-light the room. Compose a still placing the pet(s) clearly "
        "in the frame exactly as described below. " + cast +
        " Place NO other/extra animals anywhere (no stray or cloned cat/dog on any bed, shelf, "
        "scratcher, sofa, or in the background)."
        # PD 2026-06-30: the composite was rendering pets ANIMATED/illustrated and at broken
        # scale (oversized, pasted flat in the foreground, ignoring the room's perspective).
        # Enforce photographic realism + correct depth/scale explicitly.
        " PHOTOREALISM: output a REAL PHOTOGRAPH (a candid iPhone snapshot) — absolutely NOT an "
        "illustration, cartoon, anime, painting, 3D-render or CGI. The pet(s) must match the "
        "photographic realism, fur texture, grain and lighting of the provided room photo and "
        "the pet reference photos — same camera, same real-world look."
        " PERSPECTIVE & SCALE: place each pet at REALISTIC SCALE for where it stands in the room "
        "— sized correctly for its distance from the camera, paws/body planted firmly on the "
        "floor plane with correct ground contact and a soft contact shadow, aligned to the room's "
        "perspective and floor lines. Do NOT oversize the pet, do NOT paste it flat in the "
        "foreground, do NOT let it float; a real animal photographed standing in this room."
        # PD 2026-07-05: a wink still put a right-side-up front-facing dog face on a belly-up
        # (upside-down) body, so the neck twisted ~180° to reconcile them — a grotesque broken
        # neck. Any pose is fine (lying on the back is fine); what breaks is a face whose
        # up-direction disagrees with the body's. Keep head and body in ONE orientation.
        " ANATOMY: correct, natural animal anatomy — head, neck and body in ONE consistent "
        "orientation, the neck never twisted or rotated past a natural range. If the pet lies on "
        "its back or side, its face stays oriented to match that body (looking up/back is fine); "
        "do NOT force a right-side-up, front-facing face onto a belly-up or turned-away body. No "
        "extra/missing/duplicated limbs, no impossible joints."
        " 9:16 vertical, pet eye-level, natural room light."
        "\n\n" + base + ("\n" + emph if emph else ""))


# PD 2026-06-14: real casual-phone LO-FI look, baked into the prompt (not post-process).
LOFI_REALISM_DIRECTIVE = (
    "LO-FI RESOLUTION — render at the VISUAL QUALITY of a real, slightly LOW-RESOLUTION "
    "older-iPhone home video: lower resolution and finer detail than a glossy 4K AI render, "
    "real-camera compression and a touch of natural grain, natural available room light, "
    "true-to-life colors (not over-saturated, not over-smoothed, no studio gloss/HDR). "
    "CRITICAL: this lowers ONLY the picture RESOLUTION/FIDELITY — keep the FULL, lively "
    "motion, action and dynamic movement exactly as directed; do NOT slow, calm, soften or "
    "reduce the animation. Lower the image quality, NOT the movement or the story."
)

# PD 2026-07-13: the LO-FI *prompt* directive above is not enough for a real-look AV cut. When
# Seedance renders from clean real reference photos (ref mode), the output comes back glossy/HD
# no matter what the text asks — so a "real filmed" challenge/daily episode looks too plain and
# clean, not like an actual phone clip. Apply a deterministic lo-fi *grade* as a post-process on
# every real-look (non-fantasy) AV cut so it reads as genuinely filmed and blends with RF. PD
# picked the SUBTLE grade (2026-07-13 A/B sample): mild grain + slight warm/desaturation + a
# touch of softening — never heavy vignette. Fantasy cuts keep their vivid look (skipped below).
AV_LOFI_GRADE_VF = os.getenv(
    "AV_LOFI_GRADE_VF",
    "eq=contrast=1.05:saturation=0.90:brightness=0.008,colorbalance=rm=0.03:bm=-0.03,"
    "noise=alls=7:allf=t,unsharp=3:3:-0.4:3:3:0.0")


def _apply_av_lofi_grade(mp4_path: "Path", progress_cb: "ProgressCb" = None,
                         dry_run: bool = False) -> None:
    """Bake the subtle lo-fi grade onto a finished real-look AV cut, in place. No-op on
    dry_run / missing file / AV_LOFI_GRADE=0. Keeps audio out (AV cuts are silent here)."""
    if dry_run or os.getenv("AV_LOFI_GRADE", "1") == "0":
        return
    if not mp4_path.exists() or mp4_path.stat().st_size < 10_000:
        return
    graded = mp4_path.with_name(mp4_path.stem + "_lofi.mp4")
    cmd = ["ffmpeg", "-y", "-i", str(mp4_path), "-vf", AV_LOFI_GRADE_VF, "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "19",
           str(graded)]
    _run(cmd, f":film_frames: [3c/6] lo-fi grade {mp4_path.stem}", progress_cb, dry_run)
    if graded.exists() and graded.stat().st_size > 10_000:
        mp4_path.unlink(missing_ok=True)
        graded.rename(mp4_path)


# PD 2026-06-24: the lo-fi home-video look is for REALITY cuts (so AV blends with real_footage).
# An imagination / fantasy beat (a daydream, a magical world, 무릉도원·근두운) should look the
# OPPOSITE — lush and wondrous — or the dream renders as a dull, washed-out, low-res scene
# instead of a place worth escaping to. For those cuts swap lo-fi for this and drop the
# realism guards (static background, single-room spatial-lock) that fight a living dreamscape.
VIVID_FANTASY_DIRECTIVE = (
    "VIVID DREAMSCAPE — this is an imagination/fantasy beat, NOT real home footage: render it "
    "lush, richly saturated and luminous, lightly cinematic with soft glow, bloom and depth — "
    "a wondrous magical world, the opposite of a lo-fi phone snapshot. Colors vibrant and "
    "dreamy. The world itself may gently come alive (blossoms drift, light shimmers, clouds "
    "billow). Keep it unmistakably dream-like so it can't be confused with the reality cuts."
)

# Imagination/fantasy cuts get the vivid look + relaxed realism guards. The Director marks
# them with look="fantasy"; keyword hints are a fallback for hand-authored directives.
_AV_FANTASY_HINTS = (
    "무릉도원", "근두운", "몽환", "초현실", "환상", "paradise", "fantasy", "fantastical",
    "magical", "마법", "dreamscape", "dreamy", "misty", "surreal", "신비",
)


def _cut_is_fantasy(cc: dict | None) -> bool:
    """True when a cut is an imagination/fantasy beat → vivid look, no realism guards."""
    if not isinstance(cc, dict):
        return False
    look = str(cc.get("look") or cc.get("look_mode") or "").strip().lower()
    if look in ("fantasy", "vivid", "imagination", "dream", "dreamscape", "판타지", "상상"):
        return True
    blob = " ".join(str(cc.get(k) or "") for k in
                    ("motion_prompt", "regen_prompt", "scene", "beat", "description")).lower()
    return any(h.lower() in blob for h in _AV_FANTASY_HINTS)


def _resolve_costume_for_cut(cc: dict | None, manifests: dict | None) -> dict | None:
    """PD 2026-06-30 — sanctioned costume whitelist. When an episode's whole premise
    IS an outfit (우비 패션쇼 등), the Director sets a concept-level `costume_prop`
    {"wearer": ryani|leo|both, "item": "..."} (a bare string is treated as Ryani's).
    Returns the costume dict ONLY for cuts where the wearer is actually present, so the
    garment rides setup→climax consistently while other cuts stay bare-furred. This is
    a per-episode concept field (NOT a per-set flag like requires_harness), and it is
    NOT suppressed for ai_vtuber — a costume concept is an AV concept by definition."""
    if not isinstance(cc, dict):
        return None
    cp = (cc.get("costume_prop")
          or ((manifests or {}).get("concept") or {}).get("costume_prop")
          or (cc.get("background_plan") or {}).get("costume_prop"))
    if isinstance(cp, str) and cp.strip():
        cp = {"wearer": "ryani", "item": cp.strip()}
    if not (isinstance(cp, dict) and (cp.get("item") or "").strip()):
        return None
    wearer = (cp.get("wearer") or "ryani").lower()
    who = (cc.get("who") or "").lower()
    if wearer == "both" or wearer in who or (not who and wearer in ("ryani", "leo")):
        return {"wearer": wearer, "item": cp["item"].strip()}
    return None


def _costume_inject_text(costume: dict) -> str:
    """The prompt fragment that puts (and keeps) the sanctioned garment on the wearer
    while keeping everything else bare-furred. Shared by the still and motion paths so
    setup and climax describe the costume identically."""
    w, item = costume["wearer"], costume["item"]
    if w == "both":
        worn = f"Both Ryani and Leo wear {item}"
    elif w == "leo":
        worn = (f"Leo (the orange tabby cat) wears {item}; "
                "Ryani stays completely bare-furred")
    else:
        worn = (f"Ryani (the tailless black French bulldog) wears {item}; "
                "Leo stays completely bare-furred")
    return (
        worn + ". This one garment is intentional to the episode concept and MUST "
        "appear consistently — same color, shape and fit — in every cut where the "
        "wearer is shown, including the climax. It is worn over the natural pet body "
        "(four-legged, not a human pose). Aside from this one sanctioned garment the "
        "pets wear NO other clothing, NO hanbok, NO collars, NO bandanas, NO additional "
        "costumes."
    )


_SCENE_CLEAN_CACHE = ROOT / "data" / "scene_ref_clean_cache.json"


def _scene_ref_is_clean(path: Path) -> bool:
    """A scene_ref MUST be an empty room — NO live animals, NO people. A pet baked into the
    reference photo bleeds into EVERY cut that uses it (PD 2026-06-28: 거실 scene_ref에 레오가
    자고 있어 모든 컷 배경에 '스크래치 위 레오'가 따라붙었다). VLM-verify once per file (cached by
    path+mtime); a dirty library ref is then rejected so resolution falls through to the clean
    empty-room fallback (its prompt already forbids animals/people). Fail-OPEN on VLM error
    (don't block renders — the library is curated). Disable with SCENE_REF_CLEAN_CHECK=0."""
    if os.getenv("SCENE_REF_CLEAN_CHECK", "1") != "1":
        return True
    try:
        ck = f"{path}:{int(path.stat().st_mtime)}"
    except Exception:
        return True
    try:
        cache = json.loads(_SCENE_CLEAN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    if ck in cache:
        return bool(cache[ck])
    try:
        from agents.cameraman_brain import _call_gemini
        r = _call_gemini(
            "Is there any LIVE animal (cat/dog/pet) or any person/human visible in this room "
            "photo? A teddy bear or toy is NOT a live animal. Answer JSON only: "
            '{"animal": true or false, "person": true or false}',
            images=[path])
        if isinstance(r, str):
            r = json.loads(re.sub(r"^```(?:json)?|```$", "", r.strip()))
        clean = not (r.get("animal") or r.get("person"))
    except Exception as e:  # noqa: BLE001
        log.warning("scene_ref clean-check failed (%s) — allowing %s", e, path.name)
        return True
    cache[ck] = clean
    try:
        _SCENE_CLEAN_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    except Exception:
        pass
    if not clean:
        log.warning("scene_ref %s has a live animal/person — rejecting (clean fallback instead)",
                    path.name)
    return clean


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
    # 0. Explicit override (PD 2026-06-14): force a specific canonical empty-room image
    # so every cut shares the SAME room. The set_anchor → library lookup occasionally
    # missed (set_anchor not threaded into the manifest), making each render generate a
    # fresh GPT room → the bench/fireplace drifted cut-to-cut. SCENE_REF_OVERRIDE pins it.
    _ovr = os.getenv("SCENE_REF_OVERRIDE", "").strip()
    if _ovr:
        op = Path(_ovr)
        if not op.is_absolute():
            op = ROOT / op
        if op.exists() and op.stat().st_size > 10_000:
            if not _scene_ref_is_clean(op):
                log.warning("SCENE_REF_OVERRIDE %s contains a live animal/person "
                            "(honoring explicit pin, but it will bleed into every cut)", op.name)
            log.info("scene_ref OVERRIDE → %s", op.name)
            return op
        log.warning("SCENE_REF_OVERRIDE set but file missing: %s", op)
    # 1. Canonical library lookup
    if set_anchor:
        try:
            lib = json.loads((ROOT / "data" / "set_library.json").read_text(encoding="utf-8"))
            entry = lib.get(set_anchor)
            if entry and entry.get("scene_ref"):
                lib_path = ROOT / entry["scene_ref"]
                if lib_path.exists() and lib_path.stat().st_size > 10_000:
                    if _scene_ref_is_clean(lib_path):
                        log.info("scene_ref from library: %s → %s", set_anchor, lib_path.name)
                        return lib_path
                    log.warning("library scene_ref %s for %s is not a clean empty room — "
                                "falling back to a generated empty room",
                                lib_path.name, set_anchor)
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
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
            prompt=prompt,
            size="1024x1024",
            quality="high",
            n=1,
        )
        png_bytes = base64.b64decode(result.data[0].b64_json)
        fallback_out_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_out_path.write_bytes(png_bytes)
        try:
            from agents import api_ledger as _led
            _led.log_call("openai", "image", price_key="gpt_image",
                          model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
                          stage="scene_ref_fallback", card_id=os.getenv("CURRENT_CARD_ID") or None)
        except Exception:
            pass
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
    # PD 2026-06-24 (BGM 저작권 침해 재발 방지): never re-pick a track that YouTube
    # Content-ID has claimed. The ledger (data/bgm_claimed.json, written by
    # scripts/swap_bgm.py on a takedown) lists claimed filenames; claims are often
    # catalog-wide so we also drop any track sharing a claimed track's label.
    import json as _json
    try:
        _claimed = set(_json.loads((ROOT / "data" / "bgm_claimed.json").read_text(encoding="utf-8")))
    except Exception:
        _claimed = set()
    if _claimed:
        def _lbl(fn: str) -> str:
            t = fn.rsplit(".", 1)[0].split("-")
            return "lp-studio" if t[:2] == ["lp", "studio"] else (t[0] if t else fn)
        _bad_labels = {_lbl(c) for c in _claimed}
        _filtered = [c for c in candidates if c not in _claimed and _lbl(c) not in _bad_labels]
        candidates = _filtered or [c for c in candidates if c not in _claimed] or candidates
    # Deterministic varied pick: hash(card_id) → index into candidates
    import hashlib as _h
    card_id = card.get("card_id", "") or concept.get("title", "") or "default"
    seed = int(_h.sha1(card_id.encode("utf-8")).hexdigest()[:8], 16)
    # PD 2026-06-23 ("왜 또 맨날 똑같은거"): recently-used cooldown so BGM doesn't repeat.
    # Exclude the last ~12 picked tracks; pick from the fresh remainder (still deterministic
    # by card hash). Only falls back to the full list if every candidate is on cooldown.
    import json as _json
    _recent_path = ROOT / "data" / "tmp" / ".recent_bgm.json"
    try:
        _recent = [str(x) for x in _json.loads(_recent_path.read_text(encoding="utf-8"))][-12:]
    except Exception:
        _recent = []
    _fresh = [c for c in candidates if c not in _recent]
    _pool = _fresh if _fresh else candidates
    bgm_file = _pool[seed % len(_pool)]
    try:
        _recent_path.parent.mkdir(parents=True, exist_ok=True)
        _recent_path.write_text(_json.dumps((_recent + [bgm_file])[-12:]), encoding="utf-8")
    except Exception:
        pass
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

    # ── PD 2026-06-09 (B — path unification): ai_vtuber ALWAYS goes through the
    # Seedance image_to_video path, never legacy Veo t2v. The t2v path missed every
    # i2v improvement (reference-image blaze gate, 2s 여운, caption handling) → thick
    # markings + fast endings slipped through. Coerce ai_vtuber t2v → i2v, and if the
    # Director only produced veo_prompt, reuse it as the ref-mode motion_prompt so the
    # Seedance path always has a prompt. (real_footage t2v is never used.)
    if generation_mode == "text_to_video" and style == "ai_vtuber":
        log.info("B-unify: ai_vtuber t2v → Seedance i2v (ref mode); reusing veo_prompt")
        for cc in concept_cuts:
            if not (cc.get("motion_prompt") or "").strip():
                vp = (cc.get("veo_prompt") or "").strip()
                if vp:
                    cc["motion_prompt"] = vp
            cc.setdefault("seedance_mode", "ref")
        generation_mode = "image_to_video"
        if isinstance(concept, dict):
            concept["generation_mode"] = "image_to_video"

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
    # PD 2026-06-15: real_footage is VIDEO-FIRST. A photo mixed into an episode that
    # ALSO has real video is at most a ~0.5s caption-less FLASH accent — never a multi-
    # second ken-burns story cut (that reads static/생성형 and pads the concept off-target,
    # the 풀먹방→낮잠사진 drift). So when the episode has ≥1 real video cut, every photo cut
    # is hard-clamped to PHOTO_FLASH_SEC and its captions are blanked. A genuinely all-photo
    # episode (old-photo memory-lane with no video) is left alone — photos there ARE the story.
    _photo_flash_tags: set = set()
    PHOTO_FLASH_SEC = float(os.getenv("RF_PHOTO_FLASH_SEC", "0.5"))

    def _is_photo_cut(idx, it):
        _cc = concept_cuts[idx] if idx < len(concept_cuts) else {}
        _h = (_cc.get("source_hint") or "").strip().lower()
        return _h == "photo_i2v" or (it.get("asset") or {}).get("kind") == "photo"

    _n_photo = sum(1 for i, it in enumerate(cuts) if _is_photo_cut(i, it))
    _n_video = len(cuts) - _n_photo
    _rf_has_video = (style == "real_footage" and _n_video >= 1)
    # PD 2026-06-17 (FIRM): "사진컷 길게는 절대 안돼." A photo cut is NEVER a long static beat.
    # (Reverts the photo-MAJORITY "full beats" experiment — that produced 7~14s static photo
    # openers PD rejected.) When the episode has ANY real video, every photo is a ~0.5s flash;
    # a lone photo (no same-session sibling) is DROPPED, and photos are allowed only as a
    # same-session GROUP of ≥2 shown as a quick video-like burst. Story beats are VIDEO; if the
    # material is only old photos, use VIDEO from that era — don't hold a photo long.
    # A photo is never the closer — drop trailing photo(s) so the episode ends on real video.
    if _rf_has_video:
        while len(cuts) > 1 and _is_photo_cut(len(cuts) - 1, cuts[-1]):
            cuts.pop()
            concept_cuts = concept_cuts[:len(cuts)]
    # Drop a LONE photo (no same-session sibling); keep same-session ≥2 as a burst sequence.
    if _rf_has_video:
        def _photo_session(it):
            aid = (it.get("asset") or {}).get("asset_id") or ""
            m = re.match(r"med_(\d{4}_\d{2}_\d{2})", aid)
            return m.group(1) if m else None
        _photo_idxs = [i for i, it in enumerate(cuts) if _is_photo_cut(i, it)]
        _sess_ct: dict = {}
        for i in _photo_idxs:
            _sess_ct[_photo_session(cuts[i])] = _sess_ct.get(_photo_session(cuts[i]), 0) + 1
        _drop = {i for i in _photo_idxs
                 if not _photo_session(cuts[i]) or _sess_ct.get(_photo_session(cuts[i]), 0) < 2}
        if _drop:
            _keep = [i for i in range(len(cuts)) if i not in _drop]
            cuts = [cuts[i] for i in _keep]
            concept_cuts = [concept_cuts[i] for i in _keep if i < len(concept_cuts)]
            # NOTE: generate_manifests has no progress_cb in scope — referencing it
            # here raised NameError and crashed EVERY render whose concept had a
            # single-photo-flash cut to drop (the 6/18 03:00 batch lost all of 6/19).
            log.info("dropped %d single-photo-flash cut(s) "
                     "(photos sequence only when ≥2 same-session)", len(_drop))
        # re-drop any now-trailing photo so the episode still ends on real video
        while len(cuts) > 1 and _is_photo_cut(len(cuts) - 1, cuts[-1]):
            cuts.pop()
            concept_cuts = concept_cuts[:len(cuts)]
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
                # PD 2026-06-13: a real PHOTO used as a STILL ken-burns cut has NO
                # character drift — drift comes ONLY from Seedance photo_i2v GENERATION.
                # For real_footage, default to ken-burns of the REAL photo so
                # same-location photos are usable without drift. RF_PHOTO_MODE=i2v
                # restores Seedance animation; =off bans photos (legacy RF_VIDEO_ONLY).
                _rf_kb = (style == "real_footage"
                          and os.getenv("RF_PHOTO_MODE", "kenburns").lower() != "i2v")
                # PD 2026-06-15/17: in a VIDEO-FIRST RF episode a photo is only a ~0.5s flash
                # accent — clamp its duration and blank its captions below. In a photo-MAJORITY
                # montage (memory-lane) the photo is a FULL captioned story beat, NOT a flash.
                _flash = _rf_has_video
                if _flash:
                    _photo_flash_tags.add(tag)
                _pdur = (PHOTO_FLASH_SEC if _flash
                         else float(cc.get("duration_seconds") or 5))
                # A 0.5s flash needs no Seedance animation — force static ken-burns
                # (no generation cost, no drift); only a full-length photo cut (all-photo
                # memory-lane) may use Seedance i2v when RF_PHOTO_MODE=i2v.
                _src = "__photo_kb__" if (_flash or _rf_kb) else "__photo_i2v__"
                sources[tag] = {
                    "source": _src,
                    "photo_path": photo_fp,
                    "source_uuid": a.get("source_uuid") or "",  # for on-demand re-download
                    "motion_prompt": cc.get("motion_prompt", ""),
                    "seedance_seconds": 1 if _flash else int(cc.get("duration_seconds") or 5),
                    "trim_start": 0.0,
                    "trim_dur": _pdur,
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
            # PD 2026-07-04: honor the CONCEPT CUT's trim_start (a pinned re-make selects a
            # specific segment of a long clip — e.g. the 삼계탕 EATING window at 40s, skipping
            # the human face at 18s). It was read only from the asset dict `a` (always NULL for
            # a DB-looked-up clip) → every pinned trim_start silently became 0.0 and the episode
            # played the clip head. Prefer the cut's explicit request, fall back to the asset.
            ts = float(cc.get("trim_start") if cc.get("trim_start") is not None
                       else (a.get("trim_start") or 0.0))
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
            # PD 2026-06-11: min READ time per caption — was 1.8s, which let a 4s AV
            # cut carry 2 captions (~2s each) so the punchline appeared too late to
            # read ("물만 보면…참을 수가 없어요 / 캡션이 너무 늦게 나와 읽을 시간이 없어").
            # 2.5s floor ⇒ a 4s cut holds ONE caption for its whole length (starts
            # 0.1s, full read), longer cuts still fit 2-3.
            MIN_SCENE = float(os.getenv("CAPTION_MIN_SEC", "2.5"))
            try:
                _concept_dur = float(_cc_here.get("duration_seconds") or 0)
            except Exception:
                _concept_dur = 0.0
            try:
                _actual = float(sources.get(item["tag"], {}).get("trim_dur") or 0)
            except Exception:
                _actual = 0.0
            span = _actual or _concept_dur or (len(scenes) * MIN_SCENE)
            # PD 2026-06-11: ai_vtuber cuts are SPEED-RETIMED to AV_CUT_OUTPUT_SECONDS
            # after render. Time the captions to that OUTPUT length, not the writer's
            # pre-retime duration_seconds — else every caption is stretched onto a
            # longer timeline than the final clip and lands late / gets clipped.
            _av_out = float(os.getenv("AV_CUT_OUTPUT_SECONDS", "0") or 0)
            if style == "ai_vtuber" and _av_out > 0:
                span = _av_out
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

        # PD 2026-06-15: a ~0.5s photo flash accent carries NO caption (can't be read
        # that fast, and it's a visual punctuation, not a story beat). Blank it.
        if item["tag"] in _photo_flash_tags:
            captions[item["tag"]] = {"scenes": []}
            continue
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
        # PD 2026-06-13: eye-dot text REMOVED (canon: NO white dot above the eyes —
        # the old text here was hallucinating eyebrow dots into every regen) and the
        # floor-crumb behavior trait REMOVED from the always-injected preserve (it
        # made the image model render Ryani nose-down floor-sniffing in EVERY cut
        # with no scene direction; the trait is cafe-context-only per canon).
        preserve = (
            "The pet's breed, fur color, markings, AND SEX must be preserved "
            "exactly. "
            "Ryani is FEMALE (she/her, 11yo senior female French Bulldog — "
            "channel's 랴니엄마). Smooth feminine underbelly, NO male "
            "genitalia of any kind. Petite/refined feminine build, NOT "
            "muscular male. THIN narrow white blaze (a fine pencil-width "
            "line up the muzzle, between the eyes, to the forehead — NOT a thick wide splash, never thick/wide) from nose to forehead, "
            "a faint subtle eyebrow-like white mark above each eye (NOT a bold round dot), "
            "silver-grey aged muzzle, white chin, "
            "white chest patch. Only black/white/grey — no brown. "
            "The NAPE (back of the neck / behind the head), spine and back are SOLID "
            "BLACK — no white spot, dot or patch there (only the FRONT throat/chest is "
            "white; a white dot on the BACK of the neck is a hallucination — remove it). "
            "ABSOLUTELY NO TAIL (French Bulldog — her rear is bare and "
            "tailless; never render any tail). "
            "Leo is MALE (he/him, 8mo young male orange tabby — channel's "
            "아들 레오). "
            "BOTH pets bare-furred: NO collar, NO harness, NO clothing, NO "
            "accessories. The pets take ONLY the pose/action stated in the "
            "scene direction — do NOT default to nose-down floor-sniffing."
        )

        # Get regen direction from concept (priority) or generate generic
        regen_dir = (concept or {}).get("regen_direction", {})
        overall_style = regen_dir.get("overall_style", "")
        if not overall_style:
            # Fallback: generate from card tone/theme
            theme = card.get("theme", "")
            tone = card.get("tone_primary", "warm")
            overall_style = f"Cute pet illustration, {tone} mood, {theme} theme"
        # PD 2026-06-14: bake a REAL casual-phone LO-FI look into the generation itself
        # (NOT a post-process) so the AV doesn't read as glossy AI. Append unless disabled.
        if os.getenv("AV_LOFI", "1") != "0" and "LO-FI RESOLUTION" not in overall_style:
            overall_style = (overall_style + " " + LOFI_REALISM_DIRECTIVE).strip()

        regen = {
            "_base_style": overall_style,
            "_preserve_subjects": preserve,
            "_color_palette": regen_dir.get("color_palette", ""),
            "_texture": regen_dir.get("texture", ""),
            "_mood_atmosphere": regen_dir.get("mood_atmosphere", ""),
        }

        # PD 2026-06-14: SCENE LOCK for i2v regen stills. An i2v cut's still is
        # GPT text-to-image from (style + action + subjects) — it does NOT use the
        # scene_ref image (scene_ref pins only `ref`-mode cuts). So when the cut's
        # action has no location word the model INVENTS a room: the 주방 redo put
        # cut3 in a 복도 and the wink cut in a 침실 even though the episode is locked
        # to one kitchen. When the episode is pinned to ONE set (SCENE_REF_OVERRIDE
        # or an explicit concept scene_lock), prepend the canonical set description
        # to EVERY i2v cut so the room is identical in every cut.
        _scene_lock_desc = ""
        if os.getenv("SCENE_REF_OVERRIDE", "").strip() or (concept or {}).get("scene_lock"):
            _scene_lock_desc = ((concept or {}).get("set_description") or "").strip()
            _sa = (concept or {}).get("set_anchor")
            if len(_scene_lock_desc) < 30 and _sa:
                try:
                    _lib = json.loads((ROOT / "data" / "set_library.json").read_text(encoding="utf-8"))
                    _e = _lib.get(_sa, {})
                    _pb = _e.get("persistent_background") or {}
                    def _as_text(v):
                        if isinstance(v, list):
                            return "; ".join(str(x) for x in v)
                        return str(v) if v else ""
                    _bits = [_as_text(_pb.get("summary")), _as_text(_pb.get("floor_type")),
                             _as_text(_e.get("notable_details"))]
                    _scene_lock_desc = ". ".join(b for b in _bits if b)[:600]
                except Exception as e:
                    log.warning("scene-lock desc build failed: %s", e)
        _scene_lock_prefix = (
            f"SET — THE SAME ROOM IN EVERY CUT, never change or relocate the room: "
            f"{_scene_lock_desc}. " if _scene_lock_desc else "")
        if _scene_lock_prefix:
            log.info("scene lock active for i2v regen stills (%d chars)", len(_scene_lock_desc))

        for i, item in enumerate(cuts):
            tag = item["tag"]
            cc = concept_cuts[i] if i < len(concept_cuts) else {}
            mode = cc.get("seedance_mode", "i2v")
            # Skip GPT image-gen for ref/interp cuts — Seedance handles these
            # directly without a pre-generated still.
            if mode in ("ref", "interp"):
                continue
            subjects = item.get("asset", {}).get("subjects_csv", "pet")
            # PD 2026-06-13 (무더위 사고 근본원인): regen_prompt가 비면 스타일+캐릭터
            # canon만 남아 이미지 모델이 장소를 임의로(실내 마룻바닥) 채운다. Director의
            # 컷별 장면 지시(action/scene/beat)를 폴백 체인으로 반드시 주입 — 컷의
            # 장소·포즈·액션이 regen에 도달해야 멀티장소 여정이 산다.
            # The Director writes the cut's SPACE/scene into veo_prompt (rich EN scene with
            # the room) / motion_prompt / description (KO), and usually leaves
            # regen_prompt/scene/action EMPTY. Without pulling the scene from those fields
            # the still prompt loses the per-cut room → every cut of a multi-space concept
            # renders the SAME scene (083613: 6 rooms → one purple-cabinet two-shot, captions
            # 1/10). The image model renders the described setting; camera/motion verbs are
            # harmless for a still.
            per_cut_prompt = (cc.get("regen_prompt")
                              or cc.get("scene")
                              or cc.get("action")
                              or cc.get("veo_prompt")
                              or cc.get("motion_prompt")
                              or cc.get("description")
                              or item.get("action")
                              or "")
            if not per_cut_prompt:
                log.warning("regen_prompts: cut %s has NO scene direction "
                            "(regen_prompt/scene/action/veo/motion/description all empty) — "
                            "image model will invent the location", tag)
            # Fantasy/imagination still: vivid wondrous look (swap lo-fi → vivid) and no
            # single-room scene lock — the dreamscape is its own world (PD 2026-06-24).
            if _cut_is_fantasy(cc):
                _style = overall_style.replace(LOFI_REALISM_DIRECTIVE, VIVID_FANTASY_DIRECTIVE)
                if "VIVID DREAMSCAPE" not in _style:
                    _style = (_style + " " + VIVID_FANTASY_DIRECTIVE).strip()
                full_prompt = f"{_style}. {per_cut_prompt}. Featuring {subjects}. {preserve}"
            else:
                full_prompt = f"{overall_style}. {_scene_lock_prefix}{per_cut_prompt}. " \
                              f"Featuring {subjects}. {preserve}"
            full_prompt = _ensure_sink_height_lock(full_prompt)  # floor-sink guard
            # Sanctioned costume (PD 2026-06-30): keep the episode's premise garment on
            # the wearer in the still too, so an i2v cut's first frame matches the motion.
            _costume = _resolve_costume_for_cut(cc, {"concept": concept})
            if _costume:
                full_prompt = full_prompt + " " + _costume_inject_text(_costume)
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
                    result["motion_prompts"][item["tag"]] = _ensure_sink_height_lock(mp)

    return result


# ──────────────────────────────────────────────────────────────────────
# Subprocess runner
# ──────────────────────────────────────────────────────────────────────
def _clean_caption_text(s: str) -> str:
    """PD 2026-06-11: enforce the channel caption rules on one line — strip
    parentheses (no 괄호), a leading speaker label (랴니:/레오:/Ryani:), and quote
    marks that WRAP a phrase — but KEEP an apostrophe inside an English word
    (What's, they're). The earlier blunt `replace("'", "")` broke contractions
    (What's→Whats); this only removes a quote at a word boundary."""
    if not s:
        return s
    s = s.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
    s = re.sub(r"^\s*(랴니엄마|랴니|레오|Ryani|Leo)\s*[:：]\s*", "", s)
    # remove wrapping quotes (start/end or next to whitespace) but not intra-word '
    s = re.sub(r"(?<![A-Za-z])['‘’\"“”]|['‘’\"“”](?![A-Za-z])", "", s)
    return s.strip()


# Breed / anatomy / marking / render-guardrail descriptors that must NOT leak into
# viewer captions (PD 2026-06-13: "꼬리 없는 프렌치불독" appeared in a caption). Used to
# (a) sanitize the ground-truth fed to the Caption Agent, and (b) WARN if one survives.
_META_DESCRIPTOR_RE = re.compile(
    r"꼬리\s*없는|꼬리없는|프렌치\s*불독|프렌치불도그|french\s*bulldog|"
    r"오렌지\s*태비|오렌지\s*고양이|tabby|블레이즈|blaze|이마\s*줄|흰\s*가슴|"
    r"흰색?\s*얼굴\s*무늬|chest\s*marking|paw\s*marking", re.IGNORECASE)


def _strip_meta_descriptors(text: str) -> str:
    """Remove breed/anatomy/marking descriptors from a ground-truth DESCRIPTION (safe —
    it's internal context the Caption Agent re-writes from, not a finished caption).
    Collapses the leftover punctuation/space so the description stays readable."""
    if not text:
        return text
    t = _META_DESCRIPTOR_RE.sub("", text)
    t = re.sub(r"[（(]\s*[)）]", "", t)          # empty parens left by a removed term
    t = re.sub(r"[가-힣A-Za-z]+\s*=\s*[.,]?", "", t)  # dangling "랴니= ." appositive head
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s*,\s*,", ",", t)
    t = re.sub(r"\s+([.,])", r"\1", t)
    t = re.sub(r"(^[\s,.]+)|([\s,]+$)", "", t)
    return t.strip()


def _retime_cut_scenes(scenes: list, clip_dur: float, min_read: float = 2.5,
                       is_wink: bool = False) -> list:
    """PD 2026-06-11: re-time a cut's caption scenes onto its ACTUAL (post-retime)
    clip duration — cap the scene count to what fits at `min_read`s each, then
    distribute evenly + gap-free, first at 0.1s. Keeps text/order; fixes only
    start/end. This is applied to the VLM caption-rewrite output too, so the Caption
    Agent's own start/end can't re-introduce 0.5s flash captions that land too late
    to read (PD: 자막이 너무 늦게 나와 읽을 시간이 없어). Wink cuts keep their
    intentional brief caption untouched."""
    scenes = [s for s in (scenes or []) if (s.get("ko") or s.get("en"))]
    if not scenes or is_wink:
        return scenes
    # PD 2026-07-05: a real clip's caption count MUST fit its ACTUAL length at min_read —
    # the old "keep all, extend the clip at burn-time" plan silently failed for RF
    # one-takes (a 17s clip carried 12 captions, so the last 2 collapsed into the final
    # 0.5s and were unreadable — "끝에 캡션 안 읽히고 왜 끝나?"). When there are more
    # captions than the clip can hold at min_read, DROP the trailing excess so every kept
    # caption gets a readable window. (Photo montages keep their own short flashes; this is
    # for real video cuts with a known clip_dur.)
    _cd = float(clip_dur or 0)
    if _cd >= min_read:
        _fit = max(1, int(_cd / min_read))
        if len(scenes) > _fit:
            scenes = scenes[:_fit]
    span = max(_cd or (len(scenes) * min_read), len(scenes) * min_read, min_read)
    n = len(scenes)
    for j, s in enumerate(scenes):
        s["start"] = round(0.1 if j == 0 else j * span / n, 2)
        s["end"] = round((j + 1) * span / n if j < n - 1
                         else max(span - 0.05, s["start"] + 1.0), 2)
    return scenes


_RF_ACTION_SYS = (
    "You caption ONE real home clip for the pet channel 'Ryani & Leo', from N still frames "
    "sampled at known SECONDS across the clip. RYANI = a small BLACK French bulldog (white "
    "chin/chest/paws, grey muzzle, NO tail). LEO = an ORANGE tabby cat.\n"
    "Read what the pet ACTUALLY DOES across the frames and split it into sequential BEATS: "
    "a NEW beat whenever the action changes (e.g. sniffing a tree → walking on → sniffing "
    "the curb → squatting), AND — because the caption CARRIES the story — at least one beat "
    "every ~4–5s so no single line ever holds for more than ~6s (a held caption reads as a "
    "frozen, boring clip; the caption must keep moving). Aim for about ⌈clip_seconds ÷ 5⌉ "
    "beats (a 20s clip → ~4–5). Even a CALM clip has micro-beats grounded in subtle real "
    "change: a nap = 이불 파고듦 → 자리잡음 → 새근새근 → 발끝 씰룩 → 눈 스르륵; a rest = 두리번 → "
    "자세 고쳐 앉기 → 늘어짐. Never invent big motion the frames don't show, but never let one "
    "caption sit on the whole clip either. For EACH beat write ONE short, warm, casual Korean "
    "caption (+ its English) anchored to that beat's real time window.\n"
    "GROUND EVERY WORD IN THE FRAMES — this is the whole point:\n"
    "• Describe the REAL action at that moment. If the pet is walking, say moving; if "
    "sniffing, say sniffing; if sitting still, say so. Never caption an action a beat "
    "doesn't show (do NOT say '마킹/marks' over a WALKING beat — a dog marks when it "
    "STOPS and squats/lifts a leg at a pole/curb, so put a marking line only on that "
    "squat/sit beat).\n"
    "• OBJECT INTERACTION beats the generic default: if the pet has its MOUTH or PAWS ON an "
    "object — grabbing/biting/pulling/carrying a stick, branch, leaf, toy, blanket — caption "
    "THAT specific act (물다/문다/당기다/물고 간다/끌고 간다), NOT '냄새 맡는다/sniffing'. A snout "
    "buried in grass with a branch in the mouth is GRABBING/tugging the branch, not smelling "
    "dirt. Look at what the mouth/paws are DOING, not just where the head is. Don't let the "
    "episode's theme (e.g. a '킁킁이/sniffing' concept) override a beat that clearly shows a "
    "different action — caption the FRAME, not the concept.\n"
    "• Name our pets (레오/랴니), never generic '고양이/강아지'. A pet's playful inner voice "
    "(랴니 속마음: '나도 마킹! 나도 왔다감!') is welcome WHEN it fits that beat's action.\n"
    "• NO over-specification the frames can't confirm: no exact clock time (한밤중/자정 — "
    "say just 밤/낮), no exact body-spot you can't see (무릎 vs just 품), no absent props.\n"
    "• USE THE HOUSEHOLD'S REAL SNACK NAMES, not the visually-generic default: a small "
    "dried FISH treat in this home is **청어(새끼) 말린 것**, NEVER '멸치' (their fixed dried-"
    "fish snack is herring). If unsure of the exact food, say just '간식/말린 간식' — but do "
    "not fall back to '멸치'. (Leo also eats 민물새우; both share 청어.)\n"
    "• A human FACE may be in frame — never describe or caption the person; keep it about "
    "the pets.\n"
    "Return ONLY JSON: {\"beats\":[{\"start\":sec,\"end\":sec,\"ko\":\"..\",\"en\":\"..\"}]} — "
    "start/end within the clip, in order, non-overlapping, each ≥ 2.5s and ≤ ~6s long.")


def _household_knowledge_block(limit: int = 40) -> str:
    """The family's learned facts (grandma/grandpa clues + PD Q&A) as a compact block to
    inject into the caption VLMs — so the model that READS the footage knows what the family
    told us (e.g. the dried fish is 청어, not the generic '멸치'). Empty on any failure; capped
    so the VLM prompt stays bounded. Facts are harvested in slack _grandma_converse."""
    try:
        import sqlite3 as _sql
        from agents import knowledge as _kn
        con = _sql.connect(ROOT / "data" / "agent.db")
        try:
            return _kn.facts_block(con, limit=limit)
        finally:
            con.close()
    except Exception:
        return ""


# A caption carries a TIME anchor when it names WHEN the clip is from (past archive or
# the present bookend). The post-render caption stages ground on WHAT the footage shows
# and routinely drop this — flattening a memory-lane montage to present-tense (retro C13).
_TEMPORAL_MARKER = re.compile(
    r"\d+\s*년\s*전|\d+\s*개월\s*전|\d+\s*달\s*전|\d{4}\s*년|몇\s*해|그때|그\s*시절|어릴|어린\s*시절|"
    r"아기\s*때|아기\s*(랴니|레오)|입양|첫\s*(날|해|낮잠|수영|산책|만남)|지금(은|도)?|여전히|오늘도|"
    r"어느새|이젠|이제는|해가\s*갈수록|해마다|그로부터|"
    r"years?\s*ago|months?\s*ago|back\s*then|as\s*a\s*(pup|puppy|kitten)|these\s*days|"
    r"nowadays|\bstill\b|\btoday\b|first\s*(day|nap|swim|walk)",
    re.IGNORECASE)


def _asset_shoot_date(con, asset_id: str):
    """Shoot date of an asset: assets.captured_iso, else parsed from the id's
    `med_YYYY_MM_DD_` prefix (all iCloud clip ids encode the capture date, so this
    works even when the DB row/captured_iso is missing). Returns a date or None."""
    if not asset_id:
        return None
    if con is not None:
        try:
            row = con.execute("SELECT captured_iso FROM assets WHERE asset_id=?",
                              (asset_id,)).fetchone()
            if row and row[0]:
                return dt.date.fromisoformat(str(row[0])[:10])
        except Exception:
            pass
    m = re.search(r"(?:^|_)(\d{4})_(\d{2})_(\d{2})_", str(asset_id))
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _fit_writer_caps(writer_caps: list, mp4: Path) -> list:
    """Clamp the Writer's original caption scenes to the rendered clip's duration so a
    reverted caption never overruns the (possibly retimed) clip."""
    dur = 0.0
    try:
        if mp4.exists():
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 0)
    except Exception:
        dur = 0.0
    out = []
    for sc in writer_caps or []:
        ko = (sc.get("ko") or "").strip()
        if not ko:
            continue
        st = float(sc.get("start", 0.0) or 0.0)
        en = float(sc.get("end", st + 2.5) or (st + 2.5))
        if dur > 0:
            en = min(en, round(dur - 0.05, 2))
            st = min(st, max(0.0, en - 0.5))
        if en <= st:
            continue
        out.append({"start": round(st, 2), "end": round(en, 2), "ko": ko,
                    "en": (sc.get("en") or "").strip()})
    return out or [dict(s) for s in (writer_caps or [])]


def _enforce_memorylane_anchors(manifests: dict, anim_dir: Path,
                                progress_cb=None, dry_run: bool = False) -> None:
    """Deterministic memory-lane guarantee (retro C13). The post-render caption stages
    (RF action-grounded / punch-up, AV VLM rewrite) ground on WHAT the footage shows and
    drop the WHEN — so a multi-year montage (2016→2025 낮잠) ships every cut in present
    tense and the 10-year spine collapses (PD: 10년 전 클립에 현재형 캡션 → 시간 안 맞음).
    The prompt already asks for the anchor at the ends (§메모리레인) but the LLM ignores
    it; enforce it. For a multi-year memory-lane episode, restore the Writer's temporal
    OPENER and CLOSER captions when the regen stripped their time anchor. Middle cuts keep
    their action-grounded captions (anchor belongs at 처음·끝, not every cut). Lane-shared;
    no-op for a same-day episode or when the Writer never wrote an anchor."""
    if dry_run:
        return
    cap_path = Path(manifests.get("captions") or "")
    concept_cuts = (manifests.get("concept_cuts")
                    or (manifests.get("concept") or {}).get("cuts") or [])
    if not cap_path.exists() or not concept_cuts:
        return
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception:
        return
    ordered_tags = [k for k in cap.keys()
                    if not k.startswith("_") and isinstance(cap.get(k), dict)]
    if not ordered_tags:
        return
    tgt = None
    for src in ((manifests.get("concept") or {}), manifests):
        for k in ("date", "target_date", "episode_date"):
            v = src.get(k)
            if v:
                try:
                    tgt = dt.date.fromisoformat(str(v)[:10]); break
                except Exception:
                    pass
        if tgt:
            break
    if tgt is None:
        tgt = dt.date.today()
    con = None
    try:
        import sqlite3 as _sql
        _dbp = ROOT / "data" / "agent.db"
        if _dbp.exists():
            con = _sql.connect(str(_dbp))
    except Exception:
        con = None
    live = []
    for idx, tag in enumerate(ordered_tags):
        cc = concept_cuts[idx] if idx < len(concept_cuts) else {}
        if cc.get("function") == "wink_ending":
            continue
        d0 = _asset_shoot_date(con, cc.get("asset_id") or cc.get("secondary_asset_id"))
        ya = round((tgt - d0).days / 365.25, 1) if d0 else None
        live.append({"tag": tag, "years_ago": ya,
                     "writer_caps": cc.get("captions") or []})
    if con is not None:
        try:
            con.close()
        except Exception:
            pass
    dated = [x for x in live if x["years_ago"] is not None]
    if len(dated) < 2:
        return
    yrs = [x["years_ago"] for x in dated]
    if max(yrs) < 2 and (max(yrs) - min(yrs)) < 2:
        return  # not a multi-year memory-lane — leave present-tense captions alone

    def _has_marker(scenes):
        return any(_TEMPORAL_MARKER.search((sc.get("ko") or "") + " " + (sc.get("en") or ""))
                   for sc in (scenes or []))

    restored = []
    for role, x in (("opener", live[0]), ("closer", live[-1])):
        if not x or (role == "closer" and x is live[0]):
            continue
        # opener must be an actual past clip; a recent-clip closer still gets its
        # present bookend ("지금도") restored if the Writer wrote one and it was dropped.
        if role == "opener" and (x["years_ago"] or 0) < 1.0:
            continue
        cur = cap.get(x["tag"], {}).get("scenes") or []
        if _has_marker(cur) or not _has_marker(x["writer_caps"]):
            continue
        cap[x["tag"]]["scenes"] = _fit_writer_caps(x["writer_caps"],
                                                   anim_dir / f"{x['tag']}.mp4")
        restored.append(f"{x['tag']}({role})")
    if restored:
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        except Exception as e:
            log.warning("memory-lane anchor write-back failed: %s", e)
            return
        log.info("memory-lane anchor restore: %s", ", ".join(restored))
        if progress_cb:
            progress_cb(f":hourglass_flowing_sand: 메모리레인 시점 앵커 복원 — {', '.join(restored)}")


def _decide_tail_trim(times: list, visible: list, dur: float,
                      tail_min: float = 3.0, min_keep_frac: float = 0.4):
    """PURE decision (unit-tested): given per-time pet-visibility samples over a `dur`-second
    clip, return a trimmed duration when the pet is present EARLY but then EXITS frame for a
    contiguous tail ≥ tail_min (retro C14 / RF0800: a 17s clip panned off Ryani to the owner
    for its last 6s while captions kept narrating her). Return None (no trim) otherwise.
    Conservative on purpose — misfiring would gut good footage: requires the pet visible in
    the first third, an UNBROKEN absent tail, and keeping ≥ min_keep_frac of the clip.
    Uncertainty (VLM error) is fed as visible=True upstream, so we never trim on doubt."""
    if dur <= tail_min or len(times) < 3:
        return None
    order = sorted(range(len(times)), key=lambda i: times[i])
    times = [times[i] for i in order]
    visible = [bool(visible[i]) for i in order]
    if not any(v for t, v in zip(times, visible) if t <= dur / 3.0):
        return None  # not present early → whole-cut-absent case, that's the prominence gate
    vis_times = [t for t, v in zip(times, visible) if v]
    if not vis_times:
        return None
    last_vis = max(vis_times)
    if any(v for t, v in zip(times, visible) if t > last_vis):
        return None  # (last_vis is the max visible; defensive)
    if (dur - last_vis) < tail_min:
        return None  # absent tail too short to bother
    new_dur = round(last_vis + 0.6, 2)
    if new_dur / dur < min_keep_frac or new_dur >= dur - 0.3:
        return None
    return new_dur


def _rf_subject_exit_tail_trim(work_dir: Path, manifests: dict, anim_dir: Path,
                               progress_cb=None, dry_run: bool = False) -> None:
    """RF within-clip subject-CONTINUITY gate (retro C14 #1). The prominence gate only asks
    'is our pet in SOME frame of the cut', so a long clip that pans OFF the pet partway
    (RF0800) passes and ships footage where our pet isn't the subject while captions still
    narrate it. This samples each RF cut across its length and, when the pet is present early
    but exits for a contiguous tail, TRIMS the clip to the last pet-visible moment (and drops
    captions past it). Runs BEFORE the caption stages so they fit the trimmed clip. Env
    RF_SUBJECT_TAIL_TRIM=0 disables; silent + conservative (never trims on VLM error)."""
    if dry_run or os.getenv("RF_SUBJECT_TAIL_TRIM", "1") == "0":
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    cap_path = Path(manifests.get("captions") or "")
    if not api_key or not cap_path.exists():
        return
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception:
        return
    tags = [k for k in cap if not k.startswith("_") and isinstance(cap.get(k), dict)]
    if not tags:
        return
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        model = os.getenv("VLM_MODEL", "gemini-2.5-flash")
    except Exception as e:
        log.warning("subject-tail-trim: VLM init failed: %s", e)
        return
    _sys = ("Is one of OUR pets visible in this single frame? Our pets: RYANI = a small BLACK "
            "French bulldog (no tail), LEO = an ORANGE tabby cat. Answer ONLY JSON "
            "{\"pet_visible\":bool}. A clear frame showing only a person/hand/water/scenery "
            "with NO cat and NO dog = false; if any part of our cat or dog is in frame = true.")
    import tempfile as _tf
    tail_min = float(os.getenv("RF_TAIL_MIN_SEC", "3.0"))
    trimmed = []
    for tag in tags:
        mp4 = anim_dir / f"{tag}.mp4"
        if not mp4.exists():
            continue
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        except Exception:
            continue
        if dur < tail_min + 3.0:          # too short for a meaningful tail
            continue
        n = max(5, min(9, int(dur / 2.0)))
        times = [round(0.4 + (dur - 0.8) * i / (n - 1), 2) for i in range(n)]
        visible = []
        with _tf.TemporaryDirectory() as td:
            for t in times:
                fp = Path(td) / f"v{t:.1f}.jpg"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}",
                                "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(fp)],
                               check=False, timeout=15)
                v = True                   # default present → never trim on missing/uncertain
                if fp.exists() and fp.stat().st_size > 1000:
                    try:
                        resp = client.models.generate_content(
                            model=model,
                            contents=[_gt.Part.from_bytes(data=fp.read_bytes(),
                                                          mime_type="image/jpeg"),
                                      "pet visible?"],
                            config=_gt.GenerateContentConfig(
                                system_instruction=_sys, response_mime_type="application/json",
                                thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
                        v = bool(json.loads((resp.text or "{}").strip()).get("pet_visible", True))
                    except Exception:
                        v = True
                visible.append(v)
        new_dur = _decide_tail_trim(times, visible, dur, tail_min=tail_min)
        if not new_dur:
            continue
        tmp = mp4.with_suffix(".trim.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-t",
                        f"{new_dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "19", "-an", str(tmp)], check=False)
        if not (tmp.exists() and tmp.stat().st_size > 5000):
            continue
        os.replace(tmp, mp4)
        kept = [s for s in (cap[tag].get("scenes") or [])
                if float(s.get("start", 0) or 0) < new_dur - 0.3]
        for s in kept:
            if float(s.get("end", 0) or 0) > new_dur:
                s["end"] = round(new_dur - 0.05, 2)
        if kept:
            cap[tag]["scenes"] = kept
        trimmed.append(f"{tag} {dur:.1f}→{new_dur:.1f}s")
    if trimmed:
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("subject-tail-trim write-back failed: %s", e)
            return
        log.info("rf subject-exit tail trim: %s", ", ".join(trimmed))
        if progress_cb:
            progress_cb(f":scissors: 주체 이탈 tail 트림 — {', '.join(trimmed)}")


def _decide_incoherent_drops(cuts: list, memory_lane_min_days: int = 730,
                             diff_days: int = 2):
    """PURE decision (unit-tested): given ordered RF cuts [{tag,date,activity}], return the
    tags to DROP as an incoherent stitch (retro C14 #2 / RF1800: a 2018-10-29 field-explore
    cut + an unrelated 2018-10-07 dressed-walk cut fused into one episode). A coherent RF is
    either ONE outing (same/adjacent day) or a real memory-lane (cuts span years) or a
    same-activity multi-day compilation — those are EXEMPT. Only a cut from a DIFFERENT
    outing (≥ diff_days from cut1) doing a DIFFERENT activity is dropped; cut1 (theme-setter)
    is always kept. Conservative on purpose: undated cuts, span ≥ memory_lane_min_days (clear
    memory-lane), or same-activity are never dropped, so legit compilations/memory-lanes pass."""
    dated = [c for c in cuts if c.get("date")]
    if len(dated) < 2:
        return []
    span = (max(c["date"] for c in dated) - min(c["date"] for c in dated)).days
    if span >= memory_lane_min_days:
        return []                       # spans years → real memory-lane, exempt
    first = next((c for c in cuts if c.get("date")), None)
    if not first:
        return []
    fa = (first.get("activity") or "").strip().lower()
    drops = []
    for c in cuts:
        if c is first or not c.get("date"):
            continue
        if abs((c["date"] - first["date"]).days) < diff_days:
            continue                    # same/adjacent day → same outing, keep
        ca = (c.get("activity") or "").strip().lower()
        if ca and fa and ca == fa:
            continue                    # same activity across days → compilation, keep
        drops.append(c.get("tag"))
    return [t for t in drops if t]


def _rf_cross_cut_coherence_gate(manifests: dict, anim_dir: Path,
                                 progress_cb: ProgressCb = None) -> None:
    """RF cross-cut COHERENCE gate (retro C14 #2). Drops a later cut that belongs to a
    different outing (different day) doing a different activity than cut1, with no memory-lane
    through-line — the incoherent 'two unrelated clips fused' case (RF1800). Reuses the
    face-gate drop bookkeeping. Never fails on a 1-cut result (a single coherent clip is a
    valid RF). Env RF_COHERENCE_GATE=0 disables; date/activity from asset_id + assets row."""
    if os.getenv("RF_COHERENCE_GATE", "1") == "0":
        return
    cuts_meta = manifests.get("cuts") or []
    concept_cuts = manifests.get("concept_cuts") or (manifests.get("concept") or {}).get("cuts") or []
    if len(cuts_meta) < 2:
        return
    con = None
    try:
        import sqlite3 as _sql
        dbp = ROOT / "data" / "agent.db"
        if dbp.exists():
            con = _sql.connect(str(dbp))
    except Exception:
        con = None
    info = []
    for i, item in enumerate(cuts_meta):
        tag = item.get("tag")
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        aid = cc.get("asset_id") or item.get("asset_id")
        d = _asset_shoot_date(con, aid)
        act = ""
        if con is not None and aid:
            try:
                r = con.execute("SELECT activity FROM assets WHERE asset_id=?", (aid,)).fetchone()
                act = (r[0] if r else "") or ""
            except Exception:
                act = ""
        info.append({"tag": tag, "date": d, "activity": act})
    if con is not None:
        try:
            con.close()
        except Exception:
            pass
    drop = set(_decide_incoherent_drops(info))
    if not drop:
        return
    manifests["cuts"] = [c for c in cuts_meta if c.get("tag") not in drop]
    for key in ("concept_cuts",):
        lst = manifests.get(key)
        if isinstance(lst, list):
            manifests[key] = [c for c in lst
                              if (c.get("tag") or c.get("cut_tag")) not in drop]
    for t in drop:
        try:
            (anim_dir / f"{t}.mp4").unlink(missing_ok=True)
        except Exception:
            pass
        try:
            p = Path(manifests.get("captions", ""))
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                d.pop(t, None)
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    log.info("rf coherence gate: dropped incoherent cut(s) %s", ", ".join(sorted(drop)))
    if progress_cb:
        progress_cb(f":broken_chain: 컷간 일관성 — 무관 outing 컷 드롭: {', '.join(sorted(drop))}")


def _rf_action_grounded_captions(work_dir: Path, manifests: dict, anim_dir: Path,
                                 progress_cb=None, dry_run: bool = False) -> None:
    """PD 2026-07-06 (Layer 2 — upstream): write RF captions FROM the clip's observed
    action arc, anchored to WHEN each beat happens — not from thin upstream tags. For each
    real-video cut, sample frames across its length, have the VLM read the action per
    segment (sniff → walk → squat=mark) and emit one grounded caption per beat at that
    beat's window. Replaces the cut's captions in-place; the grounding gate + count-cap +
    여운 tail still run after as safety nets. Env RF_ACTION_CAPTIONS=0 disables; failures
    are silent (keep the writer's captions)."""
    if dry_run or os.getenv("RF_ACTION_CAPTIONS", "1") == "0":
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    cap_path = Path(manifests.get("captions") or "")
    if not api_key or not cap_path.exists():
        return
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception:
        return
    tags = [k for k in cap.keys() if not k.startswith("_")
            and isinstance(cap.get(k), dict) and cap[k].get("scenes")]
    if not tags:
        return
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        model = os.getenv("VLM_MODEL", "gemini-2.5-flash")
    except Exception as e:
        log.warning("action-caption: VLM init failed: %s", e)
        return
    if progress_cb:
        progress_cb(":clapper: [1a/3] 동작-그라운딩 캡션 (클립 동작 arc → 순간별 캡션)")
    min_read = float(os.getenv("CAPTION_MIN_SEC", "2.5"))
    n_written = 0
    for tag in tags:
        mp4 = anim_dir / f"{tag}.mp4"
        if not mp4.exists():
            continue
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        except Exception:
            dur = 0.0
        if dur <= 2.0:                      # too short to segment — leave writer's caption
            continue
        n_frames = max(3, min(6, round(dur / 3.5)))
        times = [round(0.3 + (dur - 0.6) * i / (n_frames - 1), 2) for i in range(n_frames)]
        parts, jpgs = [], []
        for t in times:
            jp = work_dir / f"_ac_{tag}_{t:.1f}.jpg"
            subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-ss",
                            f"{t:.2f}", "-i", str(mp4), "-frames:v", "1", "-q:v", "3",
                            str(jp)], capture_output=False, timeout=15)
            if jp.exists() and jp.stat().st_size > 1000:
                parts.append(_gt.Part.from_bytes(data=jp.read_bytes(), mime_type="image/jpeg"))
                parts.append(f"[frame at {t:.1f}s]")
                jpgs.append(jp)
        if len(jpgs) < 3:
            for jp in jpgs:
                try:
                    jp.unlink()
                except Exception:
                    pass
            continue
        parts.append(f"Clip length ≈ {dur:.1f}s. Caption the action beats.")
        # A caption-fix re-render carries PD's specific caption direction via env — honor it
        # while still grounding to the real on-screen beats (roadmap A2 caption mode).
        _pd_dir = os.getenv("PD_RERENDER_DIRECTIVE", "").strip()
        if _pd_dir:
            parts.append("PD의 캡션 수정 요청(화면 동작에 맞추되 이 방향을 최우선 반영): " + _pd_dir)
        beats = None
        _sys_grnd = _RF_ACTION_SYS + ("\n\n" + _hh_block if (_hh_block := _household_knowledge_block()) else "")
        try:
            resp = client.models.generate_content(
                model=model, contents=parts,
                config=_gt.GenerateContentConfig(
                    system_instruction=_sys_grnd, response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            d = json.loads((resp.text or "{}").strip())
            beats = d.get("beats") if isinstance(d, dict) else None
        except Exception as e:
            log.warning("action-caption VLM %s: %s", tag, e)
        finally:
            for jp in jpgs:
                try:
                    jp.unlink()
                except Exception:
                    pass
        if not isinstance(beats, list) or not beats:
            continue
        # sanitize → ordered, non-overlapping, ≥ min_read, within clip
        scenes, cur = [], 0.1
        for b in beats:
            ko = (b.get("ko") or "").strip()
            if not ko:
                continue
            st = max(cur, float(b.get("start", cur) or cur))
            en = float(b.get("end", st + min_read) or (st + min_read))
            en = max(en, st + min_read)
            en = min(en, round(dur - 0.05, 2))
            if en <= st:
                continue
            scenes.append({"start": round(st, 2), "end": round(en, 2),
                           "ko": ko, "en": (b.get("en") or "").strip()})
            cur = en
        if scenes:
            cap[tag]["scenes"] = scenes
            n_written += 1
    if n_written and not dry_run:
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("action-caption write-back failed: %s", e)
            return
    if progress_cb and n_written:
        progress_cb(f":clapper: 동작-그라운딩 캡션 — {n_written}컷 재작성 (화면 동작 기준)")


def _rf_caption_grounding_gate(work_dir: Path, manifests: dict, anim_dir: Path,
                               progress_cb=None, dry_run: bool = False) -> None:
    """PD 2026-06-13: real_footage caption GROUNDING gate (verify, don't blind-rewrite).

    The single-pass writer authors captions from thin VLM tags and SKIPS the full
    Caption-Agent rewrite (that rewrite clobbered upstream fixes). But PD caught two
    fabrications it lets through: a caption said 'Welsh corgi / Ryani's first greeting'
    over footage of a GOLDEN RETRIEVER with NO Ryani in frame; another narrated 'ears
    perked, soaking up the touch' over a hand doing a V-sign on the dog's nose. So we
    VERIFY each rendered cut against its caption and CORRECT only the cuts that
    contradict the frame — captions that already match are left untouched.

    Per cut: VLM reports which of our pets are actually visible + any other animal +
    the real action + frame usability. A cut's captions are rewritten (grounded, same
    timings, casual vlog tone) ONLY when they name a pet that isn't there, mis-identify
    an animal/breed, or describe an action the frame contradicts. Env RF_GROUNDING_GATE=0
    disables; failures are silent (keep originals)."""
    if dry_run or os.getenv("RF_GROUNDING_GATE", "1") == "0":
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return
    cap_path = Path(manifests.get("captions") or "")
    if not cap_path.exists():
        return
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("grounding gate: captions.json unreadable: %s", e)
        return
    tags = [k for k in cap.keys() if not k.startswith("_")
            and isinstance(cap.get(k), dict) and cap[k].get("scenes")]
    if not tags:
        return
    if progress_cb:
        progress_cb(":detective: [1b/3] 캡션 그라운딩 게이트 (주인공 가시성·일치 검수)")
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        model = os.getenv("VLM_MODEL", "gemini-2.5-flash")
    except Exception as e:
        log.warning("grounding gate: VLM init failed: %s", e)
        return

    _sys = (
        "You verify ONE caption against TWO frames (A = earlier, B = later) from the SAME "
        "scene of a pet-channel clip ('Ryani & Leo'). Two frames let you judge MOTION. "
        "Our two pets: RYANI = a small BLACK French bulldog (white chin/chest/paws, GREY "
        "muzzle, NO tail). LEO = an ORANGE tabby cat. Answer ONLY JSON: {\"ryani_visible\":bool, "
        "\"leo_visible\":bool, \"other_animal\":\"short desc or empty (e.g. golden retriever, "
        "corgi)\", \"frame_ok\":bool (IMAGE QUALITY only — false ONLY if the pictures are too "
        "dark, blown-out, or blurry to judge what's in them; a CLEAR frame that simply has no "
        "pet in it — a person, hand, water, or scenery — is frame_ok=TRUE, NOT false), "
        "\"moving\":bool (does the pet actually MOVE between A and B — walks/steps/comes closer/"
        "turns away/leaps? true=locomotion, false=STATIONARY i.e. stays in the same spot, "
        "sitting/lying/just eating or chewing in place), "
        "\"caption_matches\":bool (does the caption fairly describe these frames? false if it "
        "names a pet not in frame, calls a different animal/breed our pet, claims a PLACE the "
        "frames contradict (says 거실/카페/집 but clearly OUTDOORS, or says 산책길 but clearly "
        "INDOORS), OR claims an ACTION the frames contradict — ESPECIALLY a LOCOMOTION claim "
        "(걷다/다가오다/순찰/스텝/스텔스/돌아다니다/달리다/뛰다/쫓다/지나간다) while moving=false: a "
        "stationary pet captioned as patrolling/stepping/sneaking is a MISMATCH. "
        # LAYER 1 — object/body-part/action grounding (PD 2026-07-05): the caption fabricated
        # a water-bowl DRINKING over a person washing dishes, and a '엉덩이 실룩' (butt wiggle)
        # when the rear wasn't even in frame. So ALSO false when the caption highlights a
        # specific OBJECT (물그릇/장난감/간식/공/담요), a BODY PART (엉덩이/꼬리/혀/발/코), or a
        # concrete ACTION (물 마시기/설거지/씰룩/핥기/점프/파닥) that is NOT actually visible in
        # these two frames. The named thing must be ON SCREEN; if it isn't (it's off-frame,
        # or the real activity is something else entirely), caption_matches=false), "
        "\"claimed\":\"the specific object/body-part/action the caption asserts, or empty\", "
        "\"claimed_visible\":bool (is that asserted object/body-part/action actually visible in "
        "the frames? true if the caption makes no specific claim), "
        # Over-specification (PD 2026-07-05: '한밤중' when only 'night' is knowable; '무릎' when
        # it's the chest; 'sitting/focused' when the pet is walking). A caption must not assert
        # a precise detail the two frames can't confirm.
        "\"overspecified\":bool (does the caption assert a PRECISE detail the frames can't "
        "confirm — an exact time like 한밤중/자정/새벽/정오 when only day-vs-night is visible, an "
        "exact body-position/spot like 무릎/어깨 vs just 'held', or a posture the pet isn't in "
        "e.g. 'sitting/앉아/집중' while moving=true? true = over-claims beyond the frames), "
        # Subject↔name swap (PD 2026-07-12): both pets can be on screen, so visibility passes,
        # yet the caption pins the WRONG NAME on the actor — a snack-hunt narration called the
        # ORANGE CAT '랴니' and the BLACK DOG '레오' throughout. 랴니/Ryani = the DOG, 레오/Leo =
        # the CAT. If the caption credits an action/trait to a NAMED pet, check the SPECIES of
        # who's actually doing it.
        "\"subject_swapped\":bool (does the caption put our pets' names on the WRONG animals — "
        "credits 랴니/Ryani, our DOG, to something the ORANGE CAT is doing, or credits 레오/Leo, "
        "our CAT, to what the BLACK DOG is doing? true = the names are swapped onto the wrong "
        "species; false if names are absent or correctly matched to species), "
        # Already-finished source (PD 2026-07-12): a clip the family already EDITED into a
        # finished short — with baked-in caption text, stickers, hearts, sparkles, paw-print
        # graphics, or emoji overlays — must NEVER get our captions layered on top. It's a
        # finished product, not raw footage.
        "\"baked_text\":bool (does this FRAME already contain overlaid TEXT/caption words, "
        "stickers, emoji, hearts, sparkles, paw-print or other graphic decorations baked into "
        "the video itself — i.e. it is a pre-edited/finished clip, not clean raw footage? Judge "
        "only decorations that are PART OF THE IMAGE; ignore anything you're told is our caption), "
        "\"action\":\"≤12-word Korean of what ACTUALLY happens on screen — name the REAL "
        "activity even if mundane (e.g. '사람이 그릇을 설거지한다', '레오가 가만히 앉아 받아먹는다')\"}. "
        "Judge literally and strictly from the two frames.")

    def _frame_at(mp4: Path, t: float):
        jpg = work_dir / f"_gg_{mp4.stem}_{t:.1f}.jpg"
        subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(jpg)],
                       capture_output=False, timeout=15)
        return jpg if (jpg.exists() and jpg.stat().st_size > 1000) else None

    def _check_scene(mp4: Path, start: float, end: float, ko: str):
        # TWO frames (early + late) so the VLM can judge motion (stationary vs locomotion).
        span = max(float(end) - float(start), 0.0)
        ta = float(start) + min(0.3, span * 0.25)
        tb = float(end) - min(0.3, span * 0.25)
        if tb <= ta:                       # degenerate/very short scene → single mid-frame
            ta = tb = (float(start) + float(end)) / 2.0
        jpgs = [p for p in (_frame_at(mp4, ta), (_frame_at(mp4, tb) if tb != ta else None)) if p]
        if not jpgs:
            return None
        try:
            parts = []
            for lab, jp in zip(("A (earlier)", "B (later)"), jpgs):
                parts.append(_gt.Part.from_bytes(data=jp.read_bytes(), mime_type="image/jpeg"))
                parts.append(f"[frame {lab}]")
            parts.append(f'Caption shown over this scene: "{ko}". Verify it across the frames.')
        except Exception:
            return None
        finally:
            for jp in jpgs:
                try:
                    jp.unlink()
                except Exception:
                    pass
        for _ in range(2):
            try:
                resp = client.models.generate_content(
                    model=model, contents=parts,
                    config=_gt.GenerateContentConfig(
                        system_instruction=_sys, response_mime_type="application/json",
                        thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
                d = json.loads((resp.text or "{}").strip())
                if isinstance(d, list):
                    d = next((x for x in d if isinstance(x, dict)), None)
                return d if isinstance(d, dict) else None
            except Exception as e:
                log.warning("grounding gate VLM %s@%.1f: %s", mp4.stem, mid, e)
        return None

    # 1. verify each SCENE against ITS OWN frame. Scene-level (not cut-level) is required
    #    for one-take cuts: a 45s clip carries a 12-scene narrative, and 'corgi' / 'Ryani's
    #    first greeting' only contradict the frame AT those scenes' moments (Ryani appears
    #    elsewhere in the clip, so a cut-level check passes the fabrication).
    mismatched = []  # (tag, idx, vlm, ko, reason)
    pet_absent_tags = []  # cuts where NO frame shows our pet (human/scenery only)
    finished_tags = []    # cuts whose SOURCE clip already has baked-in text/stickers (finished product)
    _svs_by_tag = {}  # tag → scene_vs, for the Layer-3 opening-visibility pass
    n_scenes = 0
    _BREEDS = {"코기": "corgi", "웰시": "corgi", "corgi": "corgi",
               "리트리버": "retriever", "retriever": "retriever", "골든": "golden",
               "푸들": "poodle", "poodle": "poodle", "말티즈": "maltese",
               "비숑": "bichon", "시바": "shiba", "보더콜리": "border collie",
               "치와와": "chihuahua", "닥스훈트": "dachshund", "포메": "pomeranian"}
    for tag in tags:
        mp4 = anim_dir / f"{tag}.mp4"
        if not mp4.exists():
            continue
        # Pass 1: check every scene against its own frame (collect results).
        scene_vs = []  # (idx, ko, v)
        for idx, sc in enumerate(cap[tag]["scenes"]):
            ko = (sc.get("ko") or "").strip()
            if not ko:
                continue
            n_scenes += 1
            scene_vs.append((idx, ko, _check_scene(
                mp4, float(sc.get("start", 0.1)), float(sc.get("end", 1.0)), ko)))
        _svs_by_tag[tag] = scene_vs
        # CUT-LEVEL dominant other-animal (union across the cut's scenes that saw one) —
        # so a breed-mismatch is caught even when a given scene's OWN frame missed the
        # other dog (per-frame VLM flicker let "웰시 코기" slip when the retriever had walked
        # off that exact frame). No extra VLM calls — reuses the per-scene results.
        cut_oa = " ".join((vv.get("other_animal") or "") for _, _, vv in scene_vs
                          if vv).lower()
        # PD 2026-06-17: SUBJECT-PROMINENCE — our pet must be the frame's actual subject.
        # A clip can pass the "pet is IN the clip" selection check (subjects_csv=ryani) yet
        # be human-dominant in the frames (e.g. baby-Ryani's first water = mom wading with a
        # stick; the rendered frames show the person, not the dog). Reuse the per-scene VLM
        # visibility (no extra calls): if EVERY viewable scene of this cut shows NEITHER pet
        # AND there's no real other-animal (so it's just a human/scenery), the cut isn't our
        # content — flag it to DROP (PD: "펫 없이 캡션도 없는 동영상 자꾸 왜 넣어").
        _valid = [vv for _, _, vv in scene_vs if vv]
        _viewable = [vv for vv in _valid if vv.get("frame_ok") is not False]
        _pet_seen = any(vv.get("ryani_visible") or vv.get("leo_visible") for vv in _valid)
        if _viewable and not _pet_seen and not cut_oa.strip():
            pet_absent_tags.append(tag)
        # Finished-product source (PD 2026-07-12): if the source already carries baked-in
        # caption text / stickers / hearts, it's an edited short, not raw footage — never
        # layer our captions on it (XBeEe1saTwk did exactly that). Drop the cut.
        if any(vv.get("baked_text") for vv in _valid):
            finished_tags.append(tag)
        # Pass 2: evaluate each scene (using its own frame + the cut-level animal).
        for idx, ko, v in scene_vs:
            low = ko.lower()
            _mentioned = {b for w, b in _BREEDS.items() if w in low}
            if not v:
                # VLM failed for this scene — we don't know subject visibility, but a
                # breed the caption names that the CUT never shows is still a safe catch.
                if _mentioned and cut_oa and not any(b in cut_oa for b in _mentioned):
                    r = f"견종 불일치(캡션:{'/'.join(_mentioned)}≠화면:{cut_oa[:24]})"
                    mismatched.append((tag, idx, {"action": "", "other_animal": cut_oa},
                                       ko, r))
                    log.info("grounding gate: %s[%d] MISMATCH(cut-level) — %s | ko=%s",
                             tag, idx, r, ko)
                continue
            reasons = []
            if (("랴니" in ko) or ("ryani" in low)) and not v.get("ryani_visible"):
                reasons.append("랴니 미등장")
            if (("레오" in ko) or ("leo" in low)) and not v.get("leo_visible"):
                reasons.append("레오 미등장")
            # Names on the wrong animals (both pets visible so the checks above pass) — PD 2026-07-12.
            if v.get("subject_swapped") is True and (("랴니" in ko) or ("레오" in ko)
                                                     or ("ryani" in low) or ("leo" in low)):
                reasons.append(f"주체-이름 스왑(랴니↔레오 반대 동물에; 실제: {(v.get('action') or '')[:16]})")
            _oa_all = ((v.get("other_animal") or "").lower() + " " + cut_oa).strip()
            if _mentioned and _oa_all and not any(b in _oa_all for b in _mentioned):
                reasons.append(f"견종 불일치(캡션:{'/'.join(_mentioned)}≠화면:{(v.get('other_animal') or cut_oa)[:24]})")
            if v.get("frame_ok") is False:
                reasons.append("과노출/암부로 주인공 안보임")
            if (v.get("moving") is False
                    and any(w in low for w in ("순찰", "스텝", "스텔스", "돌아다", "걷", "다가",
                                               "달려", "달리", "뛰", "쫓", "지나", "patrol",
                                               "step", "sneak", "stealth", "walk", "run"))
                    and "이동" not in " ".join(reasons)):
                reasons.append(f"이동-주장(화면은 정지: {(v.get('action') or '')[:16]})")
            # LAYER 1: caption asserts an object/body-part/action that isn't on screen
            # (물그릇 over dishwashing, 엉덩이 실룩 with no rear in frame).
            if v.get("claimed_visible") is False and "이동" not in " ".join(reasons):
                reasons.append(f"미표시-주장({(v.get('claimed') or '')[:16]}—화면엔 {(v.get('action') or '')[:16]})")
            if v.get("overspecified") is True and not reasons:
                reasons.append(f"과잉특정(프레임 확인불가—실제: {(v.get('action') or '')[:16]})")
            if v.get("caption_matches") is False and not reasons:
                reasons.append(f"캡션≠화면({(v.get('other_animal') or v.get('action') or '')[:20]})")
            if reasons:
                mismatched.append((tag, idx, v, ko, ", ".join(reasons)))
                log.info("grounding gate: %s[%d] MISMATCH — %s | ko=%s | vlm=%s",
                         tag, idx, reasons, ko, {k: v.get(k) for k in
                         ("ryani_visible", "leo_visible", "other_animal", "caption_matches")})
    # 1b. DROP pet-absent cuts (human/scenery only — our pet is not the visible subject).
    if pet_absent_tags and os.getenv("RF_PET_VISIBILITY_GATE", "1") == "1":
        keep = [t for t in cap if t.startswith("_") or t not in pet_absent_tags]
        _remaining = [t for t in keep if not t.startswith("_")]
        _orig = len(_remaining) + len(pet_absent_tags)
        if len(_remaining) < max(2, (_orig + 1) // 2):
            # dropping these would gut the episode → fail the slot (junk 금지), don't ship a
            # remnant (launch self-heal retries; the concept may need different clips).
            raise RuntimeError(
                f"pet-absent cuts would gut the episode: {pet_absent_tags} leave only "
                f"{len(_remaining)}/{_orig} cuts with our pet visible — failing slot")
        cap = {t: cap[t] for t in keep}
        for key in ("cuts", "concept_cuts"):
            lst = manifests.get(key)
            if isinstance(lst, list):
                manifests[key] = [c for c in lst
                                  if (c.get("tag") or c.get("cut_tag")) not in pet_absent_tags]
        mismatched = [m for m in mismatched if m[0] not in set(pet_absent_tags)]
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("grounding gate: pet-absent drop write-back failed: %s", e)
        log.warning("grounding gate: dropped %d pet-absent cut(s): %s",
                    len(pet_absent_tags), pet_absent_tags)
        if progress_cb:
            progress_cb(f":scissors: 주인공-부재 컷 {len(pet_absent_tags)}개 드롭 "
                        f"(펫이 화면에 안 보이는 사람/풍경 컷): {', '.join(pet_absent_tags)}")

    # 1c. NEVER caption over an already-finished/decorated source (PD 2026-07-12): a clip
    # with baked-in text/stickers is a finished short, not raw footage. Drop those cuts; if
    # that guts the episode (the common case — the whole clip was pre-made), fail the slot so
    # launch re-selects RAW footage instead of layering our captions on a finished product.
    if finished_tags and os.getenv("RF_FINISHED_SOURCE_GATE", "1") == "1":
        keep = [t for t in cap if t.startswith("_") or t not in finished_tags]
        _remaining = [t for t in keep if not t.startswith("_")]
        if len(_remaining) < 2:
            raise RuntimeError(
                f"finished/decorated source (baked-in text/stickers): {finished_tags} — "
                f"refusing to caption over a pre-edited clip; failing slot to re-select raw footage")
        cap = {t: cap[t] for t in keep}
        for key in ("cuts", "concept_cuts"):
            lst = manifests.get(key)
            if isinstance(lst, list):
                manifests[key] = [c for c in lst
                                  if (c.get("tag") or c.get("cut_tag")) not in finished_tags]
        mismatched = [m for m in mismatched if m[0] not in set(finished_tags)]
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("grounding gate: finished-source drop write-back failed: %s", e)
        log.warning("grounding gate: dropped %d finished/decorated-source cut(s): %s",
                    len(finished_tags), finished_tags)
        if progress_cb:
            progress_cb(f":scissors: 완성/데코 소스 컷 {len(finished_tags)}개 드롭 "
                        f"(이미 텍스트·스티커 박힌 완성영상): {', '.join(finished_tags)}")

    # LAYER 3 — the HOOK must open on our pet. cand3: cut1's opening showed no Ryani (she
    # only appeared at cut2), so the episode opened on a pet-less frame. The per-CUT
    # pet-absent gate missed it because a LATER scene of that cut did show her. Check the
    # FIRST remaining cut's FIRST viewable scene: if no pet is visible there (and it isn't a
    # real other-animal intro), flag it so its caption is re-grounded (not asserting an
    # absent pet) and surface it — the clip's trim_start should have opened on the pet (an
    # upstream selection fix). Deterministic, no re-timing.
    _first = next((t for t in cap if not t.startswith("_")), None)
    if _first and _first in _svs_by_tag:
        _svs0 = [x for x in _svs_by_tag[_first] if x[2]]
        if _svs0:
            _i0, _ko0, _v0 = _svs0[0]
            if (_v0.get("frame_ok") is not False
                    and not (_v0.get("ryani_visible") or _v0.get("leo_visible"))
                    and not (_v0.get("other_animal") or "").strip()
                    and not any(m[0] == _first and m[1] == _i0 for m in mismatched)):
                mismatched.append((_first, _i0, _v0, _ko0, "오프닝-주체부재(훅에 펫 안보임)"))
                if progress_cb:
                    progress_cb(":warning: 오프닝 훅에 주체가 안 보임 — 캡션 재그라운딩 "
                                "(상류: 클립 trim이 펫에서 시작하도록 개선 필요)")

    if not mismatched:
        if progress_cb:
            progress_cb(f":white_check_mark: 그라운딩 게이트 — {n_scenes}씬 모두 화면과 일치")
        return

    # 2. rewrite ONLY the mismatched scenes, grounded in the VLM truth (one cascade call).
    payload = [{"id": f"{tag}#{idx}", "actual": v.get("action", ""),
                "ryani_visible": v.get("ryani_visible"), "leo_visible": v.get("leo_visible"),
                "other_animal": v.get("other_animal", ""), "problem": reason, "old": ko}
               for (tag, idx, v, ko, reason) in mismatched]
    sys2 = (
        "너는 반려동물 채널 'Ryani & Leo'의 캡션 교정자다. 아래 각 항목은 한 자막(scene)이 실제 "
        "화면과 어긋난 것이다(주인공 부재/오인/과노출/행동 불일치). 각 항목의 자막을 화면에 실제로 "
        "있는 것(actual)만으로 다시 써라. 규칙: "
        "▲★우리 펫은 반드시 '이름'으로 부른다 — 화면의 주황 태비 고양이 = 무조건 '레오', 검정 "
        "프렌치불독 = 무조건 '랴니'. '주황색 고양이'·'고양이'·'강아지'·'주황 태비' 같은 일반 명칭으로 "
        "우리 펫을 절대 부르지 마라(VLM 묘사를 그대로 베끼지 말고 이름으로 바꿔라). "
        "▲화면에 없는 펫을 주어로 쓰지 마라 — 안 보이면 그 펫 얘기를 하지 마라. "
        "▲다른 동물을 우리 펫으로 부르지 마라(리트리버를 '랴니'라 하지 마라). "
        "▲견종·마킹 같은 메타 설명 금지. "
        "▲★이동을 지어내지 마라 — actual이 '가만히/앉아/엎드려/제자리/받아먹는다'처럼 정지 상태면 "
        "'순찰/스텝/스텔스/돌아다닌다/다가온다/걷는다' 같은 이동 표현을 절대 쓰지 말고, 그 정지 상태 "
        "그대로(앉아 받아먹기·구경·기다리기 등) 담백히 써라. "
        "▲★반대로 actual이 '걷는다/냄새맡으며 이동/마킹한다'처럼 움직이는데 '가만히/집중/앉아'로 "
        "뭉개지 마라 — 실제 동작(냄새 맡기·이동·마킹·순찰)을 살려 써라. "
        "▲★과잉 특정 금지 — 프레임으로 확인 안 되는 정밀한 디테일을 지어내지 마라: 정확한 시각"
        "(한밤중/자정/새벽 — 그냥 '밤/낮'만 알 수 있으면 그렇게), 정확한 위치(무릎/어깨 — 확실치 "
        "않으면 '품/곁' 정도로), 없는 소품·행동. actual에 있는 것만 담백히. "
        "▲★과거(예전/아기 때) 클립이면 시점을 캡션에 명시하라 — actual에 '아기'·어린 모습·옛 정황이 "
        "보이면 '○개월 전', '아기 땐', '그때는' 같이 시점을 드러내(현재 클립과 헷갈리지 않게). "
        "▲'낯선 친구'·익명 관찰자 톤은 other_animal에 **진짜 다른 동물**(리트리버 등)이 있을 때만. "
        "other_animal이 비어있는데 우리 펫이 안 보인다고 판정됐으면 — 우리 펫(레오/랴니)을 "
        "'낯선 친구'라 부르지 마라. 그 컷의 동작·정황 위주로 담백히 쓰거나 보이는 펫 이름으로 써라. "
        "▲캐주얼 vlog 톤, 짧고 따뜻하게, 매 줄 다른 표현(반복 금지). "
        "각 항목당 ko/en 한 줄씩. JSON만: {\"scenes\":[{\"id\":\"tag#idx\",\"ko\":\"..\",\"en\":\"..\"}]}")
    try:
        from agents.llm_cascade import call_text_cascade
        import re as _re
        txt = call_text_cascade(sys2, json.dumps(payload, ensure_ascii=False),
                                max_tokens=1600).strip()
        txt = _re.sub(r"^```(?:json)?\s*", "", txt)
        txt = _re.sub(r"\s*```$", "", txt)
        new = json.loads(txt)
    except Exception as e:
        log.warning("grounding gate rewrite failed (keeping originals): %s", e)
        if progress_cb:
            progress_cb(f":warning: 그라운딩 게이트 재작성 실패 — 원본 유지 ({len(mismatched)}씬)")
        return
    by_id = {s.get("id"): s for s in (new.get("scenes") or []) if s.get("id")}
    n_fixed = 0
    for (tag, idx, _v, _ko, _r) in mismatched:
        ns = by_id.get(f"{tag}#{idx}")
        if not ns or not ns.get("ko"):
            continue
        cap[tag]["scenes"][idx]["ko"] = ns["ko"].strip()
        if ns.get("en"):
            cap[tag]["scenes"][idx]["en"] = ns["en"].strip()
        n_fixed += 1
    if n_fixed:
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        except Exception as e:
            log.warning("grounding gate: write-back failed: %s", e)
            return
    if progress_cb:
        progress_cb(f":lower_left_ballpoint_pen: 그라운딩 게이트 — {n_scenes}씬 중 {n_fixed}씬 "
                    f"화면 기준 재작성 (주인공 부재/오인 교정)")
    log.info("grounding gate: rewrote %d/%d mismatched scenes (of %d)",
             n_fixed, len(mismatched), n_scenes)


def _rf_caption_punchup(work_dir: Path, manifests: dict, anim_dir: Path,
                        progress_cb: ProgressCb = None, dry_run: bool = False) -> None:
    """PD 2026-06-15 ROOT FIX for '단순 묘사체': the RF single-pass writer authors captions
    in a grounding-first pass and skips AV's Caption-Agent/Polisher, so captions stay flat
    & descriptive ("산책은 바닥부터 꼼꼼히") no matter how many 'add wit' rules the writer
    prompt carries. This is the missing wit stage: take each already-grounded caption and
    sharpen it into 말맛/voice — WITHOUT changing the grounded facts (same who/what/where/
    when), WITHOUT inventing relationships/firsts, USING real named entities (태풍=랴니 남친
    노란개 등). Runs AFTER the grounding gate so facts are settled; punch-up only rewords."""
    if os.getenv("RF_CAPTION_PUNCHUP", "1") == "0":
        return
    cap_path = Path(manifests.get("captions") or (work_dir / "captions.json"))
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("punchup: captions.json unreadable: %s", e)
        return
    # ground-truth per cut tag (scene_description) for safety
    sc_by_tag: dict = {}
    try:
        con = _db()
        for item in (manifests.get("cuts") or []):
            tag = item.get("tag"); aid = item.get("asset_id")
            if tag and aid:
                r = con.execute("SELECT substr(scene_description,1,140) FROM assets WHERE asset_id=?",
                                (aid,)).fetchone()
                if r and r[0]:
                    sc_by_tag[tag] = r[0]
    except Exception:
        pass
    items = []
    for tag, entry in cap.items():
        if tag.startswith("_"):
            continue
        for idx, s in enumerate(entry.get("scenes") or []):
            if (s.get("ko") or "").strip():
                items.append({"id": f"{tag}#{idx}", "ko": s.get("ko", ""),
                              "en": s.get("en", ""), "screen": sc_by_tag.get(tag, "")})
    if not items:
        return
    try:
        from agents import canon as _canon
        facts = _canon.CHARACTER_FACTS + _household_knowledge_block()
    except Exception:
        facts = ""
    sys = (
        "너는 'Ryani & Leo' 펫 숏츠의 캡션 말맛 전문가다. 아래 자막들은 이미 화면과 사실이 맞게 "
        "쓰여 있다(grounded). 네 일은 **사실은 그대로 두고 '말맛'만 끌어올리는 것** — 단순 묘사체를 "
        "캐릭터 속마음·위트·반전·발랄한 한 끗으로 바꿔라. 시청자가 '읽는 맛'이 있어야 한다.\n"
        "★ 위트는 한 스푼이지 한 그릇이 아니다. 그리고 ★ 에너지는 화면(footage)에 맞춰라 — "
        "베이스 톤은 '잔잔' 고정이 아니라 **그 컷 영상의 에너지**다. 활발한 footage(물놀이·공놀이·"
        "줌이·장난)는 **깨발랄·캐주얼·느낌표**('바다? 당연히 풍덩각!', '그 쫄보 어디 갔니'); 진짜 "
        "조용한 footage(잠·멍때림)만 차분히 — 그래도 도사체는 아니다. **도사/명상/잠언/문어체/관조체 "
        "절대 금지(예외 없음)** — '~을 만났습니다', '조용히 단단하게 앞으로', '급할 것 하나 없는 눈빛', "
        "'여유 한 스푼', '예의는 잊지 않아요', '누구나 잠깐 멈추죠' 같은 엄숙·시적·관조 내레이션은 발랄한 "
        "펫 vlog를 명상 영상으로 만든다(PD가 가장 싫어함). '긴 원테이크니 마지막을 관조적으로 닫자'는 "
        "유혹도 버려라 — 그게 도사체로 새는 구멍이다; 잔잔한 클립도 발랄·따뜻하게 닫아라. 위트는 컷마다 *다른 결*로. 너는 전체 자막을 한꺼번에 보므로 **같은 장치가 "
        "겹치지 않게 분배**하라. 특히 반복 "
        "페르소나 라벨('인생 N년', 'N년차 ~', '~ 모드 ON', '체크리스트 N번', '베테랑 프로토콜')을 "
        "한 영상에서 두 번 이상 쓰지 마라 — 펫이 사람 이력서처럼 늙고 상투어가 된다. 연륜/베테랑 "
        "표현은 *한 번만* 매력적이며 랴니에게만(레오는 8개월이라 금지). EN 줄도 KO보다 더 영리해지려 "
        "과장 번역하지 마라('veteran protocol engaged' 류 금지).\n"
        "규칙: ▲who/what/where/when(시점)·등장체는 절대 바꾸지 마라(사실 고정). ▲화면/사실에 없는 "
        "관계·사건·'첫/처음/새 친구'를 새로 지어내지 마라. ▲펫은 이름(레오/랴니), 노란 친구 개는 "
        "'태풍'. ▲여러 과거 클립이 한 흐름인 메모리레인이면 시점 앵커는 **처음·끝에만**(첫 줄 'N년 "
        "전/아기 땐', 마지막 줄 '지금도/오늘도'), 가운데 컷은 행동 그대로 — 매 컷 'N년/N년차' 반복 "
        "금지. ▲마지막 줄은 **첫 줄 정서를 되받는 북엔드 payoff**면 가장 강하다. ▲KO 한 줄+EN 한 "
        "줄, 괄호/이모지 남발/대본주석 금지.\n"
        "묘사체→말맛 예(장치를 *섞어서*, 라벨 반복 금지, 깨발랄 에너지): '발을 핥아요'→'식후 양치는 "
        "꼼꼼히', '랴니가 본다'→'그 눈빛, 말이 필요 없죠', '물가에 간다'→'바다? 당연히 풍덩각!', "
        "'헤엄친다'→'이젠 물개 다 됐네 ㅋㅋ'. (❌ 도사톤 예: '폭포 앞에선 누구나 잠깐 멈추죠' — 이런 "
        "엄숙·잠언 금지.)\n"
        "참고 캐릭터 사실:\n" + facts[:1200] + "\n"
        "각 항목 id별로 더 맛깔난 ko/en. JSON만: {\"scenes\":[{\"id\":..,\"ko\":..,\"en\":..}]}")
    user = json.dumps([{"id": i["id"], "ko": i["ko"], "screen": i["screen"]} for i in items],
                      ensure_ascii=False)
    try:
        from agents.llm_cascade import call_text_cascade
        import re as _re
        txt = call_text_cascade(sys, user, max_tokens=2000).strip()
        txt = _re.sub(r"^```(?:json)?\s*", "", txt); txt = _re.sub(r"\s*```$", "", txt)
        new = {s.get("id"): s for s in json.loads(txt).get("scenes", []) if s.get("id")}
    except Exception as e:
        log.warning("punchup failed (keeping grounded captions): %s", e)
        return
    n = 0
    for tag, entry in cap.items():
        if tag.startswith("_"):
            continue
        for idx, s in enumerate(entry.get("scenes") or []):
            ns = new.get(f"{tag}#{idx}")
            if ns and (ns.get("ko") or "").strip():
                s["ko"] = ns["ko"].strip()
                if ns.get("en"):
                    s["en"] = ns["en"].strip()
                n += 1
    if n and not dry_run:
        try:
            cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("punchup write-back failed: %s", e); return
    if progress_cb:
        progress_cb(f":sparkles: 캡션 말맛 punch-up — {n}개 자막 위트 강화 (사실 유지)")
    log.info("rf caption punch-up: sharpened %d captions", n)


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
        "describe in 1-2 short Korean sentences what ACTUALLY happens. "
        # PD 2026-06-16: identity is fixed by SPECIES — the VLM was naming the cat
        # '랴니' and the dog '레오' (swapped), which shipped wrong captions. Anchor it.
        "레오 = the ORANGE TABBY CAT (고양이). 랴니 = the BLACK FRENCH BULLDOG with NO "
        "tail (강아지). The cat is ALWAYS 레오, the dog is ALWAYS 랴니 — never call the "
        "cat 랴니 or the dog 레오; identify each pet by its species. "
        "Be specific about subject positions, movements, AND any explicit sounds "
        "(짖다/왕왕/야옹/냐옹). If a pet doesn't bark or meow, do NOT mention it. "
        "Ground-truth observer only — no speculation.")
    _vlm_sys += ("\n\n" + _hh if (_hh := _household_knowledge_block()) else "")
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
        parts.append(
            "1-2 short Korean sentences describing what actually happens. "
            "Also state which pets are visible (cat / dog) and whether either is "
            "ALREADY present in the background from the first keyframe vs newly "
            "ENTERS the frame mid-clip — say e.g. '레오는 처음부터 뒤에 있음' or "
            "'레오가 중간에 프레임 안으로 들어옴'. (Needed so captions don't falsely "
            "say a pet '등장/나타남' when it was there all along.)")
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
        # PD 2026-06-13 (#3): the Editor's footage truth (when it flagged a recaption
        # mismatch for this cut) overrides the raw VLM — the VLM mis-read the RF21 clip
        # as "eats" when she can't. The Editor reasoned over action+caption, so trust it.
        _etruth = _strip_meta_descriptors(cc.get("editor_footage_truth") or "") or None
        actual = _strip_meta_descriptors(actual)
        if _etruth:
            # Hard constraint embedded in the ground-truth the Caption Agent grounds on,
            # so the closer can't slip back to a false resolution (the 동물농장 축복-ending
            # pull made it write "결국 다 먹었답니다" over can't-eat footage).
            cc["vlm_actual_action"] = (
                _etruth + (f" (VLM: {actual})" if actual else "")
                + " [필수: 화면에 없는 결과(다 먹음/성공/완료)를 사실로 단정하지 마라. "
                  "단, 마지막 캡션은 밋밋한 사실로 끝내지 말고 보여준 내용과 이어지는 따뜻한 "
                  "마무리로 — 응원('다음엔 꼭 먹자, 랴니!')이나 열린 질문('과연 먹을 수 있을까요?')"
                  "으로 닫아라. (응원·질문은 거짓 단정이 아니므로 허용)]")
            n_described += 1
        elif actual:
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
        # The wink_ending cut carries the channel's FIXED sign-off caption
        # ("오늘도 햅삐 ♥ / Happy as ever ♥") set upstream — it is the single
        # warm payoff and must land last. The Caption Agent re-run otherwise
        # describes the clip ("찡긋! 고양이의 하루~") and overwrites it, so the
        # happy ♥ no longer closes the episode. Preserve the canonical caption.
        _cc_wink = concept_cuts[idx] if idx < len(concept_cuts) else {}
        if _cc_wink.get("function") == "wink_ending":
            continue
        new_caps = new_cut.get("captions") or []
        if not new_caps:
            continue
        # PD 2026-06-11: normalize the rewritten captions' timing to the ACTUAL
        # rendered clip (post-retime) so the Caption Agent's start/end can't
        # reintroduce flash captions (the 0.5s/1.2s scenes PD flagged).
        _clip_dur = 0.0
        try:
            _mp4 = anim_dir / f"{tag}.mp4"
            if _mp4.exists():
                _clip_dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(_mp4)],
                    capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        except Exception:
            _clip_dur = 0.0
        _cc_w = concept_cuts[idx] if idx < len(concept_cuts) else {}
        _is_wink = (_cc_w.get("function") == "wink_ending")
        # PD 2026-06-11: strip parens/speaker-labels/wrapping-quotes (keep English
        # apostrophes) — the VLM rewrite kept re-adding "(랴니: …)" style decorations.
        for _sc in new_caps:
            if isinstance(_sc, dict):
                _sc["ko"] = _clean_caption_text(_sc.get("ko", ""))
                _sc["en"] = _clean_caption_text(_sc.get("en", ""))
        new_caps = _retime_cut_scenes(
            new_caps, _clip_dur,
            min_read=float(os.getenv("CAPTION_MIN_SEC", "2.5")), is_wink=_is_wink)
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


def _hold_final_caption(manifests: dict, in_dir: Path) -> None:
    """PD 2026-06-08 여운: keep the LAST cut's final caption visible until the clip
    actually ends. The last cut is extended ~2s (rf = more real footage, av = gentle
    slow) for a lingering ending; without this the caption would vanish at its
    scripted end and the linger would play caption-less. Sets the last cut's last
    scene `end` to the clip's real duration. Idempotent; fail-safe."""
    try:
        cap_path = Path(manifests.get("captions") or "")
        cuts = manifests.get("cuts") or []
        if not cap_path.exists() or not cuts:
            return
        last_tag = None
        for item in cuts:
            t = item.get("tag")
            if t and (Path(in_dir) / f"{t}.mp4").exists():
                last_tag = t  # last cut that actually rendered (skips dropped cuts)
        if not last_tag:
            return
        mp4 = Path(in_dir) / f"{last_tag}.mp4"
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(mp4)],
            capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        if dur <= 0.1:
            return
        data = json.loads(cap_path.read_text(encoding="utf-8"))
        body = data.get(last_tag)
        if not isinstance(body, dict):
            return
        scenes = body.get("scenes") or []
        if not scenes:
            return
        last = max(scenes, key=lambda s: float(s.get("start", 0) or 0))
        if float(last.get("end", 0) or 0) < dur - 0.1:
            last["end"] = round(dur, 2)
            cap_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            log.info("여운: held final caption of %s to clip end %.1fs", last_tag, dur)
    except Exception as e:
        log.warning("hold final caption failed: %s", e)


def _caption_read_time(text: str) -> float:
    """PD 2026-06-13 (#4): seconds a caption needs ON SCREEN to be READ, scaled by
    length. A long Korean line needs more than the flat 2.5s floor. Tunable via
    CAPTION_READ_BASE_SEC / CAPTION_READ_PER_CHAR / CAPTION_MIN_SEC / CAPTION_READ_MAX_SEC."""
    t = (text or "").replace("\n", " ").strip()
    base = float(os.getenv("CAPTION_READ_BASE_SEC", "0.9"))
    per = float(os.getenv("CAPTION_READ_PER_CHAR", "0.13"))
    floor = float(os.getenv("CAPTION_MIN_SEC", "2.7"))  # PD 2026-06-30: 2.5→2.7, captions flashing too fast to read
    cap = float(os.getenv("CAPTION_READ_MAX_SEC", "7.0"))
    return max(floor, min(cap, base + per * len(t)))


def _fit_caption_reading_time(manifests: dict, in_dir: Path, progress_cb=None) -> None:
    """PD 2026-06-13 (#4): caption READING TIME drives cut length — 캡션이 길면 영상도
    길어져야 한다. For each cut, if the footage isn't long enough to read every caption
    (length-scaled) plus a final 여운 tail, EXTEND the cut by holding its last frame
    (no motion distortion); then spread the scenes gap-free (each ≥ its read-time) so
    the last caption lingers to the end (여운). Generalizes _hold_final_caption, which
    only stretched the JSON and never the video (a clip ending at its last caption had
    zero 여운). Fail-safe; disable with CAPTION_FIT_EXTEND=0."""
    # Scope: only real_footage (real clips, vlog reading-pace, no blanket speed-up).
    # AV/sticker/t2v keep their existing pacing (1.3× default) — pinning tempo there
    # would change their intended rhythm. Non-RF → old behavior, zero regression.
    if (manifests.get("style") or "").lower() != "real_footage" \
            or os.getenv("CAPTION_FIT_EXTEND", "1") == "0":
        _hold_final_caption(manifests, in_dir)
        return
    try:
        cap_path = Path(manifests.get("captions") or "")
        if not cap_path.exists():
            return
        data = json.loads(cap_path.read_text(encoding="utf-8"))
        # PD 2026-07-06: the last caption ended too fast to read (the clip hard-cut ~0.8s
        # after it appeared). Give it a longer 여운 tail so the final line lingers a beat.
        tail = float(os.getenv("CAPTION_TAIL_SEC", "1.5"))
        # assemble_episode speeds each cut by _tempo_factors[tag] (else its 1.3
        # default), which would SHRINK the reading time we fit here. Captions are
        # burned PRE-speed, so we fit in cut-timeline = display × speed, and pin the
        # cut's tempo so the fit survives assembly. No explicit agent choice → 1.0.
        tempos = data.get("_tempo_factors")
        tempos = tempos if isinstance(tempos, dict) else {}
        changed = False
        for tag, body in data.items():
            if tag.startswith("_") or not isinstance(body, dict):
                continue
            scenes = [s for s in (body.get("scenes") or [])
                      if isinstance(s, dict) and (s.get("ko") or s.get("en"))]
            if not scenes:
                continue
            mp4 = Path(in_dir) / f"{tag}.mp4"
            if not mp4.exists():
                continue
            try:
                clip_dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(mp4)],
                    capture_output=True, text=True, timeout=15).stdout.strip() or 0)
            except Exception:
                clip_dur = 0.0
            if clip_dur <= 0.1:
                continue
            # PD 2026-06-13: caption READING TIME drives the cut — NEVER freeze. Fill the
            # needed display time with REAL footage first (re-trim a longer window from the
            # source clip, which usually has lots unused), and if the source is exhausted
            # SLOW the clip down (tempo<1). A frozen frame with a motion caption ("주인
            # 바라보며 속삭이죠" over a still) reads as a glitch — so no tpad freeze.
            display_needed = round(
                sum(_caption_read_time(s.get("ko") or s.get("en")) for s in scenes)
                + tail, 2)
            _src = {}
            try:
                _sp = manifests.get("sources")
                if _sp:
                    _src = (json.loads(Path(_sp).read_text(encoding="utf-8")).get(tag) or {})
            except Exception:
                _src = {}
            src_path = _src.get("source")
            t0 = float(_src.get("trim_start") or 0.0)
            src_dur = float(_src.get("src_dur") or 0.0)
            has_src = bool(src_path and src_dur > 0 and Path(src_path).exists())
            avail_real = (src_dur - t0) if has_src else clip_dur
            raw_used = min(display_needed, max(avail_real, 0.1))
            # Re-trim a LONGER real window only if the source genuinely has more footage.
            if has_src and raw_used > clip_dur + 0.3 and avail_real > clip_dur + 0.3:
                tmp = mp4.with_suffix(".fit.mp4")
                try:
                    subprocess.run(
                        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t0}",
                         "-i", str(src_path), "-t", f"{raw_used:.2f}",
                         "-vf", "scale=720:1280:force_original_aspect_ratio=increase,"
                         "crop=720:1280,setsar=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                         "-an", str(tmp)], check=True, timeout=180)
                    tmp.replace(mp4)
                    clip_dur = float(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=nw=1:nk=1", str(mp4)],
                        capture_output=True, text=True, timeout=15).stdout.strip() or clip_dur)
                except Exception as e:
                    log.warning("caption-fit re-trim failed for %s: %s", tag, e)
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            # Playback speed to fill display_needed with the (real) clip we now have. If the
            # clip is shorter than needed (source exhausted), SLOW it (speed<1, ≥ min).
            _min_speed = float(os.getenv("CAPTION_MIN_SPEED", "0.6"))
            speed = (max(_min_speed, min(1.0, round(clip_dur / display_needed, 3)))
                     if display_needed > 0.1 else 1.0)
            tempos[tag] = speed  # assemble plays at this speed (slow-down fills the time)
            # Caption timeline = RAW clip (clip_dur); after playback ×(1/speed) each scene
            # shows for its read-time. Spread gap-free; last scene holds to end (여운).
            reads = [_caption_read_time(s.get("ko") or s.get("en")) * speed for s in scenes]
            total_read = sum(reads)
            usable = max(total_read, clip_dur - tail * speed)
            scale = (usable / total_read) if total_read > 0 else 1.0
            cursor = 0.1
            n = len(scenes)
            for j, s in enumerate(scenes):
                s["start"] = round(min(cursor, max(clip_dur - 0.5, 0.1)), 2)
                cursor += reads[j] * scale
                s["end"] = round(min(clip_dur - 0.03,
                                     cursor if j < n - 1 else clip_dur - 0.03), 2)
                if s["end"] <= s["start"]:
                    s["end"] = round(min(clip_dur - 0.03, s["start"] + 0.5), 2)
            body["scenes"] = scenes
            changed = True
            if progress_cb:
                progress_cb(f":hourglass_flowing_sand: {tag} 읽을시간 {display_needed:.0f}s "
                            f"→ real {clip_dur:.0f}s · speed {speed}")
        if changed:
            data["_tempo_factors"] = tempos  # pin fitted cuts' speed (default 1.0)
            cap_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception as e:
        log.warning("fit caption reading time failed: %s", e)
        try:
            _hold_final_caption(manifests, in_dir)
        except Exception:
            pass


def _burn_captions_cmd(manifests: dict, in_dir: Path, out_dir: Path) -> list[str]:
    """Build burn_captions.py command with optional font override from Director.
    Also fits caption reading-time → cut length + 여운 (#4) — done here so all
    burn call sites get it for free."""
    _fit_caption_reading_time(manifests, in_dir)
    cmd = [
        sys.executable, "scripts/burn_captions.py",
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


def _fade_out_ending(manifests: dict, captioned_dir: Path, progress_cb=None) -> None:
    """PD 2026-06-13: end the last content cut with a gentle video (+audio) fade to
    black — caption included — so the 여운 dissolves instead of hard-cutting to the
    outro ("딱 끊김"). Runs on the freshly-burned captioned cut (idempotent: burn
    regenerates it each render). real_footage only. CAPTION_ENDING_FADE_SEC=0 disables."""
    if (manifests.get("style") or "").lower() != "real_footage":
        return
    fd = float(os.getenv("CAPTION_ENDING_FADE_SEC", "0.7"))
    if fd <= 0:
        return
    try:
        cap_path = Path(manifests.get("captions") or "")
        if not cap_path.exists():
            return
        data = json.loads(cap_path.read_text(encoding="utf-8"))
        tags = [k for k in data.keys()
                if not k.startswith("_") and isinstance(data.get(k), dict)]
        last = None
        for t in tags:
            if (Path(captioned_dir) / f"{t}.mp4").exists():
                last = t
        if not last:
            return
        mp4 = Path(captioned_dir) / f"{last}.mp4"
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(mp4)],
            capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        if dur <= fd + 0.1:
            return
        st = round(dur - fd, 2)
        has_audio = bool(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(mp4)],
            capture_output=True, text=True, timeout=15).stdout.strip())
        tmp = mp4.with_suffix(".fo.mp4")
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(mp4),
               "-vf", f"fade=t=out:st={st}:d={fd}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if has_audio:
            cmd += ["-af", f"afade=t=out:st={st}:d={fd}", "-c:a", "aac"]
        else:
            cmd += ["-an"]
        cmd += [str(tmp)]
        subprocess.run(cmd, check=True, timeout=180)
        tmp.replace(mp4)
        if progress_cb:
            progress_cb(f":city_sunset: 여운 fade-out {fd:.1f}s — {last}")
    except Exception as e:
        log.warning("ending fade-out failed: %s", e)


def _editor_guides() -> str:
    out = ""
    for rel in ("agents/prompts/editing_direction.md",
                "agents/prompts/editing_techniques.md"):
        try:
            out += "\n\n---\n" + (ROOT / rel).read_text(encoding="utf-8")
        except Exception:
            pass
    return out


def _run_editor(concept: dict, manifests: dict, lane: str,
                progress_cb=None) -> "dict | None":
    """PD 2026-06-13 (#3): the Editor agent — the only stage that judges INTENT against
    the actual FOOTAGE. Returns an EditPlan (per-cut technique/tempo/trim, reorder,
    drop) + intent_mismatch, or None (caller proceeds unedited). EDITOR_AGENT=0 off."""
    if os.getenv("EDITOR_AGENT", "1") == "0":
        return None
    cuts = manifests.get("concept_cuts") or manifests.get("cuts") or []
    if not cuts:
        return None
    try:
        sys_p = (ROOT / "agents/prompts/editor.md").read_text(encoding="utf-8") \
            + _editor_guides()
    except Exception as e:
        log.warning("editor prompt unreadable: %s", e)
        return None
    lines = [f"title: {concept.get('title','')}",
             f"narrative: {concept.get('narrative_oneliner') or concept.get('oneliner','')}",
             f"lane: {lane}", "", "CUTS (intent vs ACTUAL footage):"]
    for c in cuts:
        tag = c.get("tag") or c.get("cut_tag")
        foot = (c.get("vlm_actual_action") or c.get("action")
                or c.get("scene_description") or "")
        caps = c.get("captions") or []
        cap_txt = " / ".join((s.get("ko") or "") for s in caps if isinstance(s, dict))
        lines.append(
            f"- tag={tag} asset={c.get('asset_id')} dur={c.get('duration_seconds')}\n"
            f"  intent_beat: {c.get('beat','')}\n"
            f"  intent_caption: {cap_txt}\n"
            f"  FOOTAGE(real): {foot}")
    user = "\n".join(lines) + "\n\nReturn the EditPlan JSON only."
    try:
        from agents.llm_cascade import call_text_cascade
        txt = call_text_cascade(sys_p, user, max_tokens=1400).strip()
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
        plan = json.loads(txt)
        if isinstance(plan, list):  # LLM sometimes wraps the object in an array
            plan = next((x for x in plan if isinstance(x, dict)), None)
        if not isinstance(plan, dict):
            log.warning("editor returned non-object plan — ignoring")
            return None
        # Normalize shapes (LLMs vary: intent_mismatch may come back as a list, etc.)
        imm = plan.get("intent_mismatch")
        if isinstance(imm, list):
            imm = next((x for x in imm if isinstance(x, dict)), None)
        plan["intent_mismatch"] = imm if isinstance(imm, dict) else None
        plan["per_cut"] = [pc for pc in (plan.get("per_cut") or [])
                           if isinstance(pc, dict)]
        plan["dropped"] = [d for d in (plan.get("dropped") or [])
                           if isinstance(d, str)]
        if progress_cb:
            mm = plan.get("intent_mismatch")
            msg = f":scissors: Editor: {plan.get('episode_technique','?')}"
            if mm:
                msg += f" — ⚠️불일치: {(mm.get('what_footage_shows') or '')[:40]}"
            progress_cb(msg)
        log.info("editor plan: tech=%s dropped=%s mismatch=%s",
                 plan.get("episode_technique"), plan.get("dropped"),
                 bool(plan.get("intent_mismatch")))
        return plan
    except Exception as e:
        log.warning("editor agent failed: %s", e)
        return None


def _apply_edit_plan(manifests: dict, plan: dict, anim_dir: "Path | None" = None,
                     progress_cb=None, allow_structural: bool = True) -> None:
    """Apply an Editor EditPlan to the manifests in place: DROP / REORDER cuts, set
    per-cut tempo_factor (→ captions _tempo_factors) and trim_start/trim_dur. Mirrors
    _rf_face_gate's drop bookkeeping (cuts + concept_cuts + captions JSON). Fail-safe."""
    if not plan:
        return
    try:
        per_cut = plan.get("per_cut") or []
        dropped = set(plan.get("dropped") or [])
        # keep=false also counts as dropped
        for pc in per_cut:
            if pc.get("keep") is False and pc.get("tag"):
                dropped.add(pc.get("tag"))
        order = [pc.get("tag") for pc in sorted(
            [p for p in per_cut if p.get("keep") is not False and p.get("tag")],
            key=lambda p: p.get("order", 1e9))]
        by_tag = {pc.get("tag"): pc for pc in per_cut}
        # Safety net: never drop down to an empty episode. If the plan would remove
        # every cut (e.g. it dropped the sole cut over a caption mismatch), keep them.
        all_tags = {(_t.get("tag") or _t.get("cut_tag"))
                    for _t in (manifests.get("concept_cuts")
                               or manifests.get("cuts") or [])}
        all_tags.discard(None)
        if all_tags and not (all_tags - dropped):
            log.warning("edit plan would drop ALL cuts — ignoring drops")
            dropped = set()
            if not order:
                order = [t for t in all_tags]
        # AV (chained) passes allow_structural=False: tempo only, no drop/reorder/trim
        # (re-trimming or reordering a generated chain breaks continuity).
        if not allow_structural:
            dropped = set()
            order = []

        def _tagof(c):
            return c.get("tag") or c.get("cut_tag")

        # 0) recaption mismatch → stamp the Editor's footage truth on the cut so the
        # post-render caption rewrite corrects captions with IT (not the wrong raw VLM).
        # Done first so a later captions-file error can't skip it.
        mm = plan.get("intent_mismatch") or {}
        if str(mm.get("suggestion", "")).startswith("recaption"):
            truth = mm.get("what_footage_shows") or ""
            aid = mm.get("asset_id") or ""
            if truth:
                for key in ("concept_cuts", "cuts"):
                    for c in (manifests.get(key) or []):
                        if not aid or c.get("asset_id") == aid:
                            c["editor_footage_truth"] = truth

        # 1) trim_start / trim_dur overrides on the cut lists
        if allow_structural:
            for key in ("cuts", "concept_cuts"):
                lst = manifests.get(key)
                if not isinstance(lst, list):
                    continue
                for c in lst:
                    pc = by_tag.get(_tagof(c))
                    if not pc:
                        continue
                    if pc.get("trim_start") is not None:
                        c["trim_start"] = float(pc["trim_start"])
                    if pc.get("trim_dur"):
                        c["trim_dur"] = float(pc["trim_dur"])
                        c["duration_seconds"] = float(pc["trim_dur"])
        # 2) DROP cuts (manifests + captions JSON + mp4)
        if dropped:
            for key in ("cuts", "concept_cuts"):
                lst = manifests.get(key)
                if isinstance(lst, list):
                    manifests[key] = [c for c in lst if _tagof(c) not in dropped]
            if anim_dir:
                for t in dropped:
                    try:
                        (Path(anim_dir) / f"{t}.mp4").unlink(missing_ok=True)
                    except Exception:
                        pass
        # 3) REORDER + tempo in the captions JSON (cut order = dict order)
        _cap = manifests.get("captions")
        cap_path = Path(_cap) if _cap else None
        if cap_path and cap_path.is_file():
            data = json.loads(cap_path.read_text(encoding="utf-8"))
            meta = {k: v for k, v in data.items() if k.startswith("_")}
            body = {k: v for k, v in data.items()
                    if not k.startswith("_") and k not in dropped}
            # reorder per plan; unknown/leftover tags keep their relative order at end
            ordered = [t for t in order if t in body] + \
                      [t for t in body if t not in order]
            new = {t: body[t] for t in ordered}
            # tempo factors
            tempos = meta.get("_tempo_factors")
            tempos = tempos if isinstance(tempos, dict) else {}
            for pc in per_cut:
                t, tf = pc.get("tag"), pc.get("tempo_factor")
                if t and t in new and tf:
                    try:
                        s = float(tf)
                        if 0.5 <= s <= 2.0:
                            tempos[t] = s
                    except (TypeError, ValueError):
                        pass
            if tempos:
                meta["_tempo_factors"] = tempos
            cap_path.write_text(json.dumps({**meta, **new}, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        # 4) reorder the cut lists to match
        rank = {t: i for i, t in enumerate(order)}
        for key in ("cuts", "concept_cuts"):
            lst = manifests.get(key)
            if isinstance(lst, list):
                lst.sort(key=lambda c: rank.get(_tagof(c), 1e9))
        if progress_cb and (dropped or order):
            progress_cb(f":scissors: EditPlan 적용 — drop={len(dropped)} 재배열={len(order)}컷")
    except Exception as e:
        log.warning("apply edit plan failed: %s", e)


_SINK_CUES = ("세면대", "sink", "washbasin", "wash basin", "basin")
_SINK_LOCK_TOKENS = ("mounted", "벽에 고정", "vanity", "counter height", "counter-height",
                     "hand-washing height", "cm above", "built into", "built-in",
                     "rim sits", "카운터 높이", "세면대 높이")
_SINK_HEIGHT_LOCK = (
    " IMPORTANT — sink mount: the sink / washbasin is BUILT INTO the bathroom vanity "
    "at adult hand-washing height; its rim sits about 80cm ABOVE the tiled floor, set "
    "against the wall, with the vanity cabinet, legs and plumbing clearly visible BELOW "
    "the basin. The basin is NEVER resting on the floor — if a pet is inside the basin "
    "it is elevated at counter height, not grounded.")


def _ensure_sink_height_lock(prompt: str) -> str:
    """Deterministic sink-height guard (PD 2026-06-23, '욕실 세면대 바닥 사건' 재발).

    Seedance / gpt-image keep grounding a bathroom sink onto the FLOOR unless the
    prompt explicitly states its mount height. The rule is in director_shots.md +
    the Validator, but the Director/LLM keep omitting it (ep 200548 cut4 again put
    Ryani inside a floor-level basin). So enforce it in CODE — same principle as the
    era-mix gate: any prompt referencing a sink/세면대/basin that lacks an explicit
    height-lock token gets the canonical mount-height clause appended. No-op when a
    lock is already present or no sink is mentioned."""
    if not prompt:
        return prompt
    pl = prompt.lower()
    if not any(c.lower() in pl for c in _SINK_CUES):
        return prompt
    if any(t.lower() in pl for t in _SINK_LOCK_TOKENS):
        return prompt
    log.info("sink-height guard: auto-injected counter-mount lock (prompt mentioned a sink w/o height)")
    return prompt.rstrip() + _SINK_HEIGHT_LOCK


def _lint_seedance_prompt(prompt: str) -> list[str]:
    """PD 2026-06-08: Seedance is expensive and weak at backgrounds — surface
    rigor gaps BEFORE the spend so we know why a render came out wrong. Log-only
    (the hard background BLOCK lives in the Validator, pre-render). Checks the
    prompt has a VERY detailed, camera-sweep background + the key guardrails."""
    p = prompt or ""
    pl = p.lower()
    issues = []
    bg_kw = sum(1 for k in (
        "wall", "floor", "window", "light", "ceiling", "sofa", "bench", "piano",
        "table", "vanity", "sink", "tile", "cabinet", "shelf", "curtain", "door",
        "rug", "mat", "바닥", "벽", "창", "조명", "천장") if k in pl)
    if len(p) < 400 or bg_kw < 5:
        issues.append(f"THIN background (len={len(p)}, bg_terms={bg_kw}) — PD wants a "
                      "camera-sweep room description (walls→floor→furniture→light, "
                      "each element one by one); Seedance will freelance the room")
    if ("ryani" in pl or "랴니" in pl or "french bulldog" in pl) and "no tail" not in pl:
        issues.append("Ryani present but missing 'NO tail' marking canon")
    if "static" not in pl and "background objects" not in pl:
        issues.append("no background-stillness guardrail")
    if issues:
        log.warning("Seedance prompt lint (%d): %s", len(issues), " | ".join(issues))
    return issues


def _ondemand_regen_still(scene_prompt: str, out_png: Path) -> Path | None:
    """PD 2026-06-08: when a Seedance call has no image, GENERATE a scene-specific
    still (OpenAI gpt-image-1 → Gemini fallback, per generate_scene) from the prompt
    + character refs and use THAT — far better than a generic static ref sheet, since
    it matches the actual scene + characters. Returns the written png, or None on
    failure (caller then falls back to a static ref)."""
    try:
        from scripts.generate_character_scene import (generate_scene,
                                                      build_character_prompt)
        who, _ = _who_and_emph(scene_prompt)
        subjects = who if who in ("ryani", "leo", "both") else "both"
        full = build_character_prompt(scene_prompt, subjects)
        data = generate_scene(full, None, subjects=subjects)
        if not data:
            return None
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(data)
        if out_png.exists() and out_png.stat().st_size > 1000:
            log.info("preflight: generated on-demand regen still (%s) → %s",
                     subjects, out_png.name)
            return out_png
        return None
    except Exception as e:
        log.warning("on-demand regen still failed: %s", e)
        return None


def _seedance_preflight(cmd: list[str]) -> list[str]:
    """PD 2026-06-08 cost guard: NEVER let a Seedance call go out without at least
    ONE image visual (text-only generation wastes the expensive call and drifts).
    If no valid image: PREFER generating a scene-specific regen still (GPT→Gemini)
    over a generic static ref (PD: "차라리 regen 이미지 써도 되지 않아?"); fall back
    to the canonical pair ref only if generation fails; if even that is missing,
    REFUSE (raise) rather than spend. Also lint the prompt for background richness."""
    cmd = list(cmd)

    def _val(flag):
        if flag in cmd:
            i = cmd.index(flag)
            if i + 1 < len(cmd):
                return cmd[i + 1]
        return None

    mode = _val("--mode") or "i2v"
    img_flags = ("--image", "--last-frame", "--ref-image")
    has_image = any(
        i + 1 < len(cmd) and cmd[i + 1] and Path(cmd[i + 1]).exists()
        for i, a in enumerate(cmd) if a in img_flags
    )
    if not has_image:
        fb_img = None
        how = ""
        prompt = _val("--prompt") or ""
        out = _val("--output") or ""
        # 1) PREFER a freshly generated, scene-specific still (OpenAI→Gemini).
        if prompt and out:
            still = Path(out).with_name(Path(out).stem + "_preflight_still.png")
            fb_img = _ondemand_regen_still(prompt, still)
            how = "generated regen still"
        # 2) last resort: canonical static ref sheet.
        if not fb_img:
            pair = ROOT / REF_LIBRARY.get("pair", "")
            fb_img = pair if pair.exists() else None
            how = "fallback pair ref"
        if not fb_img:
            raise RuntimeError(
                "Seedance preflight: no image and could not generate one — refusing "
                "a text-only generation (cost guard, PD 2026-06-08).")
        if mode == "i2v":
            if "--image" in cmd:
                cmd[cmd.index("--image") + 1] = str(fb_img)
            else:
                cmd += ["--image", str(fb_img)]
        else:  # ref / interp
            cmd += ["--ref-image", str(fb_img)]
        log.warning("Seedance preflight: no valid image → %s (%s)", how,
                    Path(fb_img).name)
    _lint_seedance_prompt(_val("--prompt") or "")
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
    # PD 2026-06-08: guarantee ≥1 image + lint prompt on every real Seedance dispatch.
    if len(cmd) >= 2 and "animate_seedance_i2v.py" in str(cmd[1]):
        # PD 2026-06-10 COST GUARD: hard per-process Seedance ceiling.
        global _SEEDANCE_CALL_COUNT
        _budget = int(os.getenv("SEEDANCE_MAX_CALLS", "40"))
        _SEEDANCE_CALL_COUNT += 1
        if _SEEDANCE_CALL_COUNT > _budget:
            raise RuntimeError(
                f"SEEDANCE 예산 초과: 이 프로세스에서 i2v 호출 {_SEEDANCE_CALL_COUNT} > "
                f"SEEDANCE_MAX_CALLS={_budget}. 비용 폭주 방지로 더 이상 렌더하지 않음 "
                f"(PD 2026-06-10). 의도적이면 env로 상향.")
        if progress_cb:
            progress_cb(f":coin: Seedance 호출 {_SEEDANCE_CALL_COUNT}/{_budget}")
        cmd = _seedance_preflight(cmd)
    # Cost ledger (PD 2026-06-25): record every billable VIDEO dispatch (Seedance + Veo)
    # so the morning report can show where money went + the re-render multiplication.
    # Best-effort; never blocks a render.
    try:
        from agents import api_ledger as _led
        _vid = _led.classify_video_cmd(cmd)
        if _vid:
            _prov, _svc, _pk, _mdl = _vid
            _led.log_call(_prov, _svc, price_key=_pk, model=_mdl, stage=step,
                          card_id=os.getenv("CURRENT_CARD_ID") or None)
    except Exception:
        pass
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
            sys.executable, "scripts/animate_seedance_i2v.py",
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
            "(she/her). Markings CONSISTENT: THIN narrow white "
            "blaze (a fine pencil-width line up the muzzle, between the eyes, to the forehead — NOT a wide splash, never thick/wide) from nose to forehead, "
            "a faint subtle eyebrow-like white mark above each eye (NOT a bold round dot), "
            "silver-grey aged muzzle, white chin, "
            "large white chest patch, bat ears, ABSOLUTELY NO TAIL, petite "
            "refined feminine body (NOT muscular), only black/white/grey — no "
            "brown. The NAPE (back of the neck) / spine / back are SOLID BLACK — no "
            "white spot or patch there (white is the FRONT throat/chest only). "
            "Keep her EXACTLY as in the source photo."
        )
        if "white blaze" not in prompt:
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


def _cut_face_ok(mp4_path: Path, n_frames: int = 4) -> bool:
    """PD 2026-06-10: per-cut FACE-INTEGRITY gate. SEPARATE from the marking gate
    (`_cut_character_ok`) — a melted/distorted face or a floating orb artifact can
    have 'correct' markings, so the marking gate passes it (it passed 003111's
    orb-face, and so did Giri at 9/10). Focused VLM call on FACE-CROPPED frames:
    cropping the head region is what made this reliable (full frames diluted the
    VLM's attention and the small orb slipped through). Returns True = face OK,
    False = clear AI face corruption (caller swaps the cut to a real clip).
    Fail-open (True) on error / no API."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        from PIL import Image as _Img
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
                fp = Path(td) / f"f{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path),
                                "-frames:v", "1", str(fp)], check=False, timeout=20)
                if not fp.exists():
                    continue
                img = _Img.open(fp).convert("RGB")
                img = img.crop((0, 0, img.width, int(img.height * 0.62)))  # head region
                if max(img.size) > 1024:
                    r = 1024 / max(img.size)
                    img = img.resize((int(img.width * r), int(img.height * r)))
                cp = Path(td) / f"c{i}.jpg"
                img.save(cp, format="JPEG", quality=88)
                parts.append(_gt.Part.from_bytes(data=cp.read_bytes(),
                                                 mime_type="image/jpeg"))
            if not parts:
                return True
            prompt = (
                "These are FACE-CROPPED frames from ONE rendered animal cut (a still "
                "photo animated by AI, which can corrupt the face). Flag ONLY clear AI "
                "corruption: a melted / smeared / distorted muzzle or eyes, grossly "
                "asymmetric or mismatched eyes, a face that warps unnaturally, or a "
                "floating white blob / orb / dot artifact stuck on the face or "
                "forehead. Do NOT flag a real, natural face for being sleepy, "
                "eyes-closed, motion-blurred, side-profile, or low-light. Return ONLY "
                'JSON {"face_defect": true|false, "detail": "<what/where, or empty>"}.'
            )
            parts.append(prompt)
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"), contents=parts,
                config=_gt.GenerateContentConfig(response_mime_type="application/json"))
            t = (resp.text or "").strip()
            t = re.sub(r"^```(?:json)?\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
            data = json.loads(t)
            # The model sometimes returns a per-frame LIST instead of one object.
            if isinstance(data, list):
                hits = [d for d in data if isinstance(d, dict) and d.get("face_defect")]
                defect = bool(hits)
                detail = hits[0].get("detail", "") if hits else ""
            elif isinstance(data, dict):
                defect = bool(data.get("face_defect"))
                detail = data.get("detail", "")
            else:
                defect = False
                detail = ""
            if defect:
                log.info("cut face gate: DEFECT on %s — %s",
                         Path(mp4_path).name, detail)
                return False
            return True
    except Exception as e:
        log.warning("cut face gate failed for %s: %s", mp4_path, e)
        return True


def _source_face_visible(photo_path, who: str) -> bool:
    """PD 2026-06-12: BEFORE animating a photo_i2v cut, check whether the named
    pet's FACE is clearly visible in the SOURCE still. If Ryani is prone /
    face-away / head buried, Seedance i2v INVENTS a wrong face the moment the
    motion lifts her head (PD: "랴니가 엎드려서 첫 씬에서 얼굴 안 보일 때도 랴니 얼굴
    결국 만들거면, 생성할 때 정면 샷을 꼭 써야지"). When the face is NOT visible the caller
    switches to ref mode with a frontal Ryani reference instead of trusting the
    source. Returns True = face clearly visible (i2v from source is safe),
    False = hidden (use frontal ref). Fail-OPEN (True) on no API / non-photo /
    error — never block a render."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or who not in ("ryani", "leo", "both"):
        return True
    p = Path(photo_path)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp"}.get(p.suffix.lower())
    if not p.exists() or not mime:  # skip heic / unknown — fail open
        return True
    # We only need to guard RYANI's face (Leo's cat face drifts less and PD's
    # complaint is specifically Ryani). If the cut is leo-only, nothing to check.
    target = ("the small black French Bulldog (Ryani — white muzzle blaze)"
              if who in ("ryani", "both") else None)
    if not target:
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        prompt = (
            f"In this source photo, is the FACE of {target} clearly visible and "
            "roughly frontal or 3/4 (both the eyes and the muzzle showing) — i.e. "
            "NOT lying face-down, NOT turned fully away, NOT with the head hidden / "
            "buried / cropped out? Judge the FACE only. Return ONLY JSON "
            '{"face_visible": true|false}.'
        )
        parts = [_gt.Part.from_bytes(data=p.read_bytes(), mime_type=mime), prompt]
        resp = client.models.generate_content(
            model=os.getenv("VLM_MODEL", "gemini-2.5-flash"), contents=parts,
            config=_gt.GenerateContentConfig(response_mime_type="application/json"))
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (resp.text or "").strip())
        data = json.loads(t)
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        return bool(data.get("face_visible", True)) if isinstance(data, dict) else True
    except Exception as e:
        log.warning("source face check failed for %s: %s", photo_path, e)
        return True


def _prerender_photo_kenburns_cuts(manifests: dict, work_dir: Path,
                                   progress_cb: ProgressCb = None,
                                   dry_run: bool = False) -> None:
    """PD 2026-06-13: real_footage photo cuts marked `source = "__photo_kb__"` are
    rendered as a gentle KEN-BURNS of the REAL photo (ffmpeg zoompan, NO Seedance) —
    so a same-location photo is usable WITHOUT the character drift that Seedance
    photo_i2v generation causes. Outputs to work_dir/photo_i2v/<tag>.mp4 (so the later
    trim step picks it up) and rewrites the sources entry to that mp4."""
    if dry_run:
        return
    sources_path = Path(manifests["sources"])
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    work_anim = work_dir / "photo_i2v"
    work_anim.mkdir(parents=True, exist_ok=True)
    n = 0
    for tag, entry in list(sources.items()):
        if not isinstance(entry, dict) or entry.get("source") != "__photo_kb__":
            continue
        photo_path = entry.get("photo_path")
        if (not photo_path or not Path(photo_path).exists()) and entry.get("source_uuid"):
            photo_path = _ensure_local(photo_path, entry.get("source_uuid")) or photo_path
        if not photo_path or not Path(photo_path).exists():
            log.warning("photo_kb: photo not found for %s", tag)
            continue
        dur = float(entry.get("trim_dur") or entry.get("seedance_seconds") or 6)
        out_mp4 = work_anim / f"{tag}.mp4"
        frames = max(25, int(dur * 25))
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-loop", "1",
                 "-i", str(photo_path), "-t", f"{dur:.2f}",
                 "-vf", ("scale=720:1280:force_original_aspect_ratio=increase,"
                         "crop=720:1280,"
                         f"zoompan=z='min(zoom+0.0006,1.10)':d={frames}:s=720x1280:fps=25,"
                         "setsar=1"),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out_mp4)],
                check=True, timeout=120)
            sources[tag] = {"source": str(out_mp4), "trim_start": 0.0, "trim_dur": dur,
                            "has_human": entry.get("has_human", 0)}
            n += 1
            if progress_cb:
                progress_cb(f":frame_with_picture: 켄번즈(실사 사진) 컷 {tag} (드리프트 없음)")
        except Exception as e:
            log.warning("photo_kb render failed for %s: %s", tag, e)
    if n:
        sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        log.info("photo_kb: %d real-photo ken-burns cuts pre-rendered", n)


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
        # Who is in this cut? (used by the frontal-ref guard below AND the
        # per-cut marking gate further down — compute once.)
        who = ""
        for ccx in (manifests.get("concept_cuts") or []):
            if (ccx.get("tag") or ccx.get("cut_tag")) == tag:
                who = (ccx.get("who") or "").lower(); break
        # PD 2026-06-12: if Ryani is in this cut but her face is HIDDEN in the
        # source still (prone / face-away), animating it i2v makes Seedance invent
        # a wrong face when the motion lifts her head. Switch to ref mode with the
        # frontal Ryani reference (ryani_solo.png) instead of trusting the source.
        # BytePlus can't mix first_frame + reference_image, so ref mode drops the
        # source scene — an accepted trade for a correct face. Gate: PHOTO_I2V_FRONTAL_REF.
        frontal_refs: list = []
        if (os.getenv("PHOTO_I2V_FRONTAL_REF", "1") != "0" and not dry_run
                and who in ("ryani", "both")
                and not _source_face_visible(photo_path, "ryani")):
            _rr = _resolve_ref("ryani_solo")
            if _rr:
                frontal_refs = [_rr]
                if who == "both":
                    _lr = _resolve_ref("leo_solo")
                    if _lr:
                        frontal_refs.append(_lr)
                if progress_cb:
                    progress_cb(f":bust_in_silhouette: {tag} — 소스에 랴니 얼굴 안 보임, "
                                "정면 레퍼런스(ref 모드)로 생성")
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
            if frontal_refs:
                # ref mode: frontal character reference(s), NO source first_frame.
                cmd = [
                    sys.executable, "scripts/animate_seedance_i2v.py",
                    "--mode", "ref",
                    "--prompt", prompt,
                    "--seconds", seconds,
                    "--model", photo_model,
                    "--output", str(out_mp4),
                ]
                for _rp in frontal_refs:
                    cmd.extend(["--ref-image", str(_rp)])
            else:
                cmd = [
                    sys.executable, "scripts/animate_seedance_i2v.py",
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
            marking_bad = (who in ("ryani", "leo", "both")
                           and not _cut_character_ok(out_mp4, who))
            # PD 2026-06-10: ALSO swap when the face itself is AI-corrupted (orb /
            # melted), regardless of markings — this is the gap that let 003111's
            # orb-face through (markings were 'fine'). Runs on every photo_i2v cut.
            face_bad = not _cut_face_ok(out_mp4)
            if marking_bad or face_bad:
                reason = "얼굴 왜곡(orb/녹음)" if face_bad else "랴니 마킹 드리프트"
                repl = _find_replacement_real_clip(who or "both")
                if repl:
                    sources[tag] = repl
                    log.info("photo_i2v %s: %s → replaced with real clip %s",
                             tag, reason, repl.get("asset_id"))
                    if progress_cb:
                        progress_cb(f":arrows_counterclockwise: {tag} {reason} "
                                    f"— 실제 영상으로 교체")
                elif progress_cb:
                    progress_cb(f":warning: {tag} {reason}, 교체 클립 없음 — photo_i2v 유지")
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
            sys.executable, "scripts/animate_seedance_i2v.py",
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


def _ensure_local(file_path: str, source_uuid: str | None, *, force: bool = False) -> str | None:
    """PD 2026-06-07 efficient model: a clip's original may have been pruned to
    save space. If the file is missing but we have its Photos UUID, re-download
    it on demand to the same path. Returns a valid local path or None."""
    try:
        if file_path and Path(file_path).exists():
            return file_path
        # GCS-first (PD 2026-06-21): a missing original is pulled from the GCS mirror —
        # fast, reliable, no Photos-library open / PhotoKit / dawn window / osxphotos lock
        # (the whole reason on-demand iCloud downloads failed). Safe mid-render, so it runs
        # BEFORE the RENDER_NO_ICLOUD_FETCH gate; osxphotos below is now only the fallback
        # for assets not yet mirrored.
        if file_path:
            try:
                from icloud import gcs as _gcs
                if _gcs.enabled():
                    got = _gcs.download_to(file_path)
                    if got:
                        return got
            except Exception as _e:
                log.warning("gcs ensure_local fetch failed for %s: %s", file_path, _e)
        # PD 2026-06-15: osxphotos downloads happen ONLY in the controlled upfront prefetch
        # (force=True), never mid-render. RENDER_NO_ICLOUD_FETCH=1 makes mid-render
        # _ensure_local calls fail fast (caller drops the cut) instead of triggering a
        # serialized per-photo osxphotos loop that hung the batch 8h (av 0/2). The prefetch
        # passes force=True so iCloud-only photos ARE downloaded first (PD: 받아서 쓰면 됨).
        if not force and os.getenv("RENDER_NO_ICLOUD_FETCH", "0") == "1":
            return None
        if not source_uuid:
            return file_path  # nothing we can do — let caller handle missing
        from icloud.sync import _osxphotos_available, download_asset_by_uuid
        if not _osxphotos_available():
            # Cloud VM / non-Mac: no Photos library to pull from. GCS was already tried
            # above; if the asset wasn't there, treat it as missing (caller drops/swaps).
            return file_path
        import time as _time
        dest = Path(file_path).parent if file_path else (ROOT / "data" / "assets" / "clips")
        # PD 2026-06-10: under the concurrent launch batch (an RF cut and an AV cut
        # export from the Photos library at the same time) a single osxphotos export
        # can transiently fail and return None — which used to kill the whole AV slot
        # (av went 0/2 on 6/11 this way). Retry with backoff so transient library
        # contention doesn't drop the cut; the same download succeeds in isolation.
        dl = None
        for _attempt in range(3):
            try:
                dl = download_asset_by_uuid(source_uuid, dest)
            except Exception as _e:
                log.warning("download_asset_by_uuid attempt %d failed for %s: %s",
                            _attempt + 1, source_uuid, _e)
                dl = None
            if dl:
                break
            if _attempt < 2:
                _time.sleep(1.5 * (_attempt + 1))  # 1.5s, then 3.0s backoff
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


def _rf_dehaze_vf(src_path: Path, trim_start: float, trim_dur: float) -> str:
    """De-haze filter for a SOFT/blurry real-footage clip, or "" if it's already sharp.

    PD 2026-07-10: grandma films on a phone whose lens is sometimes smudged, so some
    real clips arrive soft/hazy (the dirty-lens veil kills fine detail). We recover it
    the way PD approved for RF2100 — a gentle unsharp + a touch of contrast (NO saturation
    boost: de-haze recovers detail, not color) — but ONLY when the clip is genuinely soft,
    so a sharp clip is never over-sharpened
    (that adds halos). Softness is measured as low high-frequency energy (variance of a
    Laplacian) on a few normalized frames — this is a BLUR signal, not a contrast one:
    the dirty-lens footage measured lapVar≈600 vs ≈1200 corrected vs ≈7000 for a
    genuinely crisp clip, so the default 800 threshold cleanly separates soft from sharp.
    RF-only: AV cuts are Seedance-generated and never carry a real lens's haze.
    Knobs: RF_DEHAZE(=1), RF_DEHAZE_LAPVAR(=800), RF_DEHAZE_VF(override the filter).
    Non-fatal: any probe error returns "" (never blocks a render for a cosmetic pass).
    """
    if os.getenv("RF_DEHAZE", "1") != "1":
        return ""
    try:
        import numpy as _np
        import tempfile as _tf
        from PIL import Image as _PImg
        thresh = float(os.getenv("RF_DEHAZE_LAPVAR", "800"))
        # Below this the frame is near-black / static / extreme-blur — unsharp there just
        # amplifies noise instead of recovering detail, so leave it alone (a soft clip
        # with recoverable detail sits in the band, e.g. RF2100 ≈600).
        floor = float(os.getenv("RF_DEHAZE_LAPVAR_MIN", "40"))
        dur = max(0.1, float(trim_dur or 0.0))
        times = [trim_start + f * dur for f in (0.2, 0.5, 0.8)]
        vals = []
        with _tf.TemporaryDirectory() as td:
            for i, t in enumerate(times):
                fp = Path(td) / f"h{i}.jpg"
                try:
                    _extract_frame(src_path, max(0.0, t), fp)
                    if not fp.exists():
                        continue
                    im = _PImg.open(fp).convert("L")
                    W = 1080
                    h = max(2, int(im.height * W / max(1, im.width)))
                    a = _np.asarray(im.resize((W, h)), dtype=_np.float64)
                    lap = (a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2]
                           + a[1:-1, 2:] - 4 * a[1:-1, 1:-1])
                    vals.append(float(lap.var()))
                except Exception:
                    continue
        if not vals:
            return ""
        med = sorted(vals)[len(vals) // 2]
        if med >= thresh or med < floor:
            return ""
        # De-haze recovers DETAIL (sharpness), not color: a soft/dirty-lens clip has
        # veiled fine detail, not weak saturation. An added saturation boost just makes
        # the recovered clip read as over-processed (PD 2026-07-17: RF0800 opened on a
        # soft past clip whose saturation had been pushed too high — unnecessary), so the
        # default no longer touches saturation. Sharpness + a touch of contrast only.
        vf = os.getenv(
            "RF_DEHAZE_VF",
            "unsharp=5:5:0.9:5:5:0.0,eq=contrast=1.09:gamma=0.98")
        log.info("rf de-haze: soft clip %s (lapVar=%.0f < %.0f) → %s",
                 src_path.name, med, thresh, vf)
        return vf
    except Exception as e:
        log.warning("rf de-haze probe failed (%s) — skipping", e)
        return ""


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
        # LAST cut 여운 (PD 2026-06-08, CORRECTED): NOT a freeze. Play ~2s MORE of
        # the ACTUAL real footage past the caption — the video keeps moving and the
        # caption stays alive over it (the final caption is held to clip end later by
        # _hold_final_caption). "마지막을 그냥 멈추는게 아니라 동영상을 2초 더 넣으라고
        # 캡션을 보여주는 상태에서." Only if the source clip genuinely runs out do we
        # pad the shortfall by a brief freeze (last resort).
        if tag == last_body_tag:
            cap_end = float(cap_end_by_tag.get(tag, 0))
            linger = float(os.getenv("RF_END_LINGER_S", "2.0"))
            src_dur = entry.get("src_dur")
            avail = (float(src_dur) - trim_start) if src_dur else None
            natural = max(trim_dur, cap_end + 0.3)   # caption must at least finish
            want = natural + linger                  # +2s of real video past that
            play = want if avail is None else min(want, avail)
            trim_dur = play
            shortfall = max(0.0, want - play)        # footage ran out this much
            if shortfall > 0.15:
                vf, time_args = _build_edit_effect_filter(
                    "freeze_to_caption_end", trim_dur, extra_pad=shortfall)
                effect = "freeze_to_caption_end"
                log.info("last cut %s — play %.1fs real + %.1fs freeze (footage short)",
                         tag, play, shortfall)
            else:
                vf, time_args = _build_edit_effect_filter("static", trim_dur)
                effect = "static"
                log.info("last cut %s — 여운: play %.1fs real footage (+%.1fs linger)",
                         tag, play, linger)
        else:
            vf, time_args = _build_edit_effect_filter(effect, trim_dur)

        # PD 2026-06-06 HARD RULE: a human FACE must NEVER be visible. If this
        # cut's clip has a human, crop them out. Prefer the VLM-computed
        # pets-only window (reliable — keeps pets, excludes the face); fall back
        # to the writer's directional crop_out hint, then a center zoom. Runs
        # BEFORE the edit_effect.
        crop_hint = by_tag_crop.get(tag, "")
        has_human = bool(entry.get("has_human"))
        # PD 2026-06-12 HARD RULE: a human FACE leaked into a render because the
        # intra-clip/one-take cuts didn't carry has_human, so the face-crop never ran.
        # ALWAYS confirm has_human from the DB by the source path — every RF path must
        # crop the human out, never exclude/skip the clip.
        if not has_human:
            try:
                _rel_src = str(src_path).replace(str(ROOT) + "/", "")
                _r = _db().execute(
                    "SELECT has_human FROM assets WHERE file_path=? OR file_path=? "
                    "OR asset_id LIKE ? LIMIT 1",
                    (str(src_path), _rel_src, "%" + src_path.stem.split("_")[-1])
                ).fetchone()
                if _r and _r[0]:
                    has_human = True
                    log.info("trim %s: has_human via DB lookup → crop face out", tag)
            except Exception:
                pass
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

        # ROTATION (PD 2026-06-08, revised 2026-07-05): a rotated source (iPhone portrait
        # stored as 1920×1080 + rotation=-90) must reach display orientation BEFORE the crop —
        # else crop hits the raw landscape frame and the output is sideways. The old fix baked it
        # with a manual transpose + `-noautorotate`, but that LEFT the stale display-matrix on the
        # trimmed clip, so every downstream re-encode (burn_captions, assemble) auto-rotated it a
        # SECOND time → sideways again (the 260706_RF0800 bug). On modern ffmpeg (7.x+ — both the
        # VM's static build and the Mac's) DEFAULT autorotate already inserts the rotation as the
        # FIRST filter (crop is correct) AND strips the display matrix from the re-encoded output
        # (no stale metadata → nothing double-rotates). So we let autorotate run and do NOT
        # transpose or pass -noautorotate. Verified: a -90 clip → clean 1080×1920, rotation=none.
        transpose_vf = ""  # rely on ffmpeg default autorotate (see above); no manual bake

        # Landscape-source reframing for the 9:16 frame (FINAL vf step). Two modes:
        #  - letterbox (DEFAULT, PD 2026-06-17): show the WHOLE landscape (fit width, black
        #    top/bottom) so the subject is NEVER cropped out — a 9:16 crop of wide footage
        #    kept losing 랴니 ("랴니가 안 보이는 샷이 너무 많다"). Full frame, subject smaller.
        #  - panscan (RF_LANDSCAPE_MODE=panscan): fill by cropping + a horizontal sweep (no
        #    black bars, fuller screen) but can crop the subject out at the sides.
        # A portrait clip already fills 9:16, so neither applies. RF_PANSCAN=0 disables both
        # (assemble then letterboxes at concat anyway). RF_LANDSCAPE_MODE=off also disables.
        _land_mode = os.getenv("RF_LANDSCAPE_MODE", "letterbox").lower()
        if os.getenv("RF_PANSCAN", "1") == "0":
            _land_mode = "off"
        if _land_mode != "off":
            _dw, _dh = _probe_display_dims(src_path)
            _is_land = bool(_dw and _dh and (_dw / _dh) > (720.0 / 1280.0))
            if _is_land:
                if _land_mode == "panscan":
                    _reframe = _panscan_fill_filter(
                        trim_dur, is_landscape=True, pan=not bool(crop_vf))
                else:  # letterbox (default)
                    _reframe = _letterbox_fill_filter()
                vf = ",".join(p for p in (vf, _reframe) if p)

        # PD 2026-07-10: gently de-haze a SOFT clip (smudged phone lens) — measured
        # per-clip, applied FIRST so it recovers detail on the raw frame before crop/
        # reframe. Sharp clips read above threshold and are left untouched. RF-only.
        if not dry_run:
            _dehaze_vf = _rf_dehaze_vf(src_path, trim_start, trim_dur)
            if _dehaze_vf:
                vf = ",".join(p for p in (_dehaze_vf, vf) if p)

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
            # PD 2026-06-13: a 3-frame probe was too sparse for a long cut — a person
            # moving into frame BETWEEN samples leaked an eye-nose region the static
            # crop never knew to avoid (face slipped through at 37.9s of an 11s cut,
            # caught only by the post-render facecheck's 1.5s grid). Sample at the SAME
            # ~1.5s density so the face_union covers the face wherever/whenever it
            # appears across the whole cut. Capped to bound VLM calls.
            _probe_step = float(os.getenv("FACE_CROP_PROBE_STEP", "1.5"))
            _nprobe = max(3, min(12, int(span / _probe_step) + 1))
            offsets = [min(max(0.1, span - 0.1), span * (i + 0.5) / _nprobe)
                       for i in range(_nprobe)]
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
                # PD 2026-06-13: the crop only has to hide the FIDO-recognizable
                # region (EYES + NOSE), NOT the whole head. "코 아래만 보이는 건 OK."
                # Shrink the avoid-target to the TOP band of the face box (eyes+nose
                # ≈ upper 60%) so the crop excludes ONLY that — keeping a WIDE framing
                # instead of zooming into the pet to remove the entire face (ep 234320
                # turned into an extreme fur close-up). The lower face (mouth/chin,
                # below the nose) is allowed to stay in frame.
                _avoid_top = float(os.getenv("FACE_AVOID_TOP_FRAC", "0.6"))
                face_union = {"x": x0, "y": y0,
                              "w": x1 - x0, "h": (y1 - y0) * _avoid_top}
                human = face_union  # slide the window away from the eyes+nose band
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
        # PD 2026-06-12: the model often returns PIXELS despite being asked for 0..1
        # fractions (pets {x:260,y:381,w:724...}). Treating those as fractions put the
        # pet bbox far off-frame → the face-crop failed. Normalize to fractions if any
        # value looks like pixels (>1.5).
        def _norm_box(b):
            if not b:
                return b
            vals = [float(b.get(k, 0) or 0) for k in ("x", "y", "w", "h")]
            if max(vals) > 1.5:
                return {"x": vals[0] / W, "y": vals[1] / H,
                        "w": vals[2] / W, "h": vals[3] / H}
            return b
        pets = _norm_box(pets)
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
        # PD 2026-06-12 HARD RULE (face crop, verified): the loose "contain the whole
        # pet" window above can still include the human face. SHRINK a 9:16 window
        # (zoom toward the pet) and SHIFT it until it EXCLUDES the face_union — even at
        # the cost of cropping the pet's BODY (a tight pet-head close-up; a partial
        # chin of the human is OK, recognizable EYES are not). Returns "" if no zoom
        # excludes the face (human holding the pet) → the post-render face gate drops it.
        pet_cx = px + pwp / 2.0
        pet_cy = py + php / 2.0
        fu = face_union
        if fu:
            fux = float(fu["x"]) * W; fuy = float(fu["y"]) * H
            fuw = float(fu["w"]) * W; fuh = float(fu["h"]) * H

            def _ov(wx, wy, ww, wh):
                return not (wx >= fux + fuw or wx + ww <= fux
                            or wy >= fuy + fuh or wy + wh <= fuy)
            chosen = None
            # PD 2026-06-13: floor the zoom. The loop used to shrink to 0.34 to exclude a
            # face — a 35%-width window that lost Ryani entirely ("처음에 너무 크롭해서 랴니가
            # 안 보임"). Stop at ~0.56: if no window that big can drop the FIDO region, return
            # "" and let the post-render face gate handle it, rather than over-cropping the pet
            # out of frame. (A back-of-head isn't a FIDO face so face_box returns None → no
            # shrink at all — "사람 뒤통수는 괜찮아".)
            _min_frac = float(os.getenv("FACE_CROP_MIN_FRAC", "0.56"))
            _fracs = tuple(f for f in (0.96, 0.88, 0.80, 0.72, 0.64, 0.56, 0.48, 0.40, 0.34)
                           if f >= _min_frac)
            for frac in _fracs:
                wh = H * frac
                ww = wh * r
                if ww > W:
                    ww = W; wh = ww / r
                wx = min(max(pet_cx - ww / 2.0, 0.0), W - ww)
                wy = min(max(pet_cy - wh / 2.0, 0.0), H - wh)
                if not _ov(wx, wy, ww, wh):
                    chosen = (ww, wh, wx, wy); break
                # shift away from the face (right-of / left-of, below / above),
                # keeping the pet centre inside the window
                for sx in (fux + fuw, fux - ww):
                    sx = min(max(sx, 0.0), W - ww)
                    if sx <= pet_cx <= sx + ww and not _ov(sx, wy, ww, wh):
                        chosen = (ww, wh, sx, wy); break
                if chosen:
                    break
                for sy in (fuy + fuh, fuy - wh):
                    sy = min(max(sy, 0.0), H - wh)
                    if sy <= pet_cy <= sy + wh and not _ov(wx, sy, ww, wh):
                        chosen = (ww, wh, wx, sy); break
                if chosen:
                    break
            if not chosen:
                log.info("vlm crop: face inseparable from pet in %s — drop via gate",
                         src_path.name)
                return ""   # face on the pet → post-render face gate drops the cut
            cw, ch, cx, cy = chosen
        cw = int(cw) // 2 * 2; ch = int(ch) // 2 * 2
        cx = min(max(0, int(cx)), W - cw); cy = min(max(0, int(cy)), H - ch)
        return f"crop={cw}:{ch}:{cx}:{cy}"
    except Exception as ex:
        log.warning("vlm crop failed for %s: %s", src_path.name, ex)
        return ""


def _probe_display_dims(path: Path) -> tuple[int, int]:
    """Display (rotation-corrected) width,height of a video. (0,0) on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        rot = 0
        try:
            rot = int(float(_probe_rotation(path)))
        except Exception:
            rot = 0
        if rot in (90, -90, 270, -270):
            w, h = h, w   # rotated → display dims swap
        return w, h
    except Exception:
        return 0, 0


def _panscan_fill_filter(dur: float, is_landscape: bool, pan: bool = True,
                         W: int = 720, H: int = 1280) -> str:
    """PD 2026-06-11: fill the 9:16 frame with NO black letterbox, and for a
    LANDSCAPE source PAN horizontally across its width over the clip so the sides
    are revealed over time ("꽉 채우되 좌우로 움직여서 더 보여주도록") — instead of
    assemble's decrease+pad (which boxed landscape clips into a small center strip).
    Orientation is decided in Python so the ffmpeg expr has NO conditionals/commas
    (those broke parsing). Output is exactly WxH so the concat normalize pads nothing.
    - landscape: scale to fill HEIGHT (width ≥ W), crop a WxH window; pan x sweeps
      0 → (in_w-W) over `dur` (pan=True; t never exceeds dur so no clamp needed),
      else centered.
    - portrait/square: scale to fill WIDTH (height ≥ H), center-crop the height."""
    # PD 2026-06-15: scale with force_original_aspect_ratio=increase so the frame is
    # ALWAYS ≥ WxH before cropping — a plain `scale=-2:H` / `scale=W:-2` could leave the
    # other dim < target on odd/small sources, so `crop=720:1280` failed ("too big or
    # non positive size"), crashing the whole RF render. increase guarantees crop fits.
    scale = f"scale={W}:{H}:force_original_aspect_ratio=increase"
    if is_landscape:
        if pan and dur and dur > 0.1:
            # CENTERED partial sweep across the middle `frac` of the available width,
            # so the subject (usually centre-framed) stays in view while the sides are
            # revealed — a full 0→edge pan walked the subject out of frame. Tunable.
            frac = max(0.1, min(1.0, float(os.getenv("RF_PAN_FRACTION", "0.5"))))
            a = (1.0 - frac) / 2.0
            x = f"(in_w-{W})*({a:.3f}+{frac:.3f}*t/{dur:.2f})"
        else:
            x = f"(in_w-{W})/2"
        return f"{scale},crop={W}:{H}:{x}:(in_h-{H})/2"
    return f"{scale},crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2"


def _letterbox_fill_filter(W: int = 720, H: int = 1280) -> str:
    """PD 2026-06-17: fit the WHOLE landscape source into the 9:16 frame with black bars
    top/bottom, instead of cropping it to fill. A landscape clip cropped to 9:16 (panscan)
    only shows a vertical slice, so the subject kept getting cropped out — "랴니가 안 보이는
    샷이 너무 많다". Letterbox shows the entire frame (가로를 폭에 맞추고 위아래 블랙) so the
    subject is ALWAYS in view, just smaller. Output is exactly WxH (concat pads nothing)."""
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")


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
        # LAST-RESORT only (PD 2026-06-08): used when the real clip ran out of footage
        # and we can't extend the 여운 with real video. Freeze the final frame for
        # `extra_pad` sec. NO fade-out — the caption must stay visible over the hold
        # (the 여운 is "caption alive while video continues", not a fade to black).
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
    # PD 2026-06-17: do NOT ship a GUTTED episode. A 6-cut memory-lane ("9년 전과 오늘",
    # 2016~2023 랴니) rendered as a single sparse cut because 5 old iCloud clips didn't
    # download in time and were silently dropped — PD saw a flat "한 컷 롱테이크". An
    # episode that loses most of its cuts is broken; fail the slot (junk 금지) so it stays
    # empty / self-heals, instead of shipping the remnant. Keep ≥ half the cuts (min 2).
    _orig = len(remaining) + len(dropped)
    # Earlier stages (prefetch / grounding gate / editor-plan) can silently drop
    # cuts BEFORE this guard, shrinking the picture so a GUTTED slot looks like a
    # small concept and slips through (카페 ep 5컷 → 2컷 shipped: the 3 prefetch-
    # failed cuts were already gone here, so _orig read as 2 and the floor never
    # fired). Honor the LARGEST original cut count still visible anywhere in the
    # manifests so the floor reflects the real concept, not the gutted remnant.
    for _src in (manifests.get("concept_cuts"),
                 (manifests.get("concept") or {}).get("cuts"),
                 (manifests.get("payload") or {}).get("cuts")):
        if isinstance(_src, list):
            _orig = max(_orig, len(_src))
    _floor = max(2, (_orig + 1) // 2)
    if len(remaining) < _floor:
        raise RuntimeError(
            f"gutted render: only {len(remaining)}/{_orig} cuts survived "
            f"(dropped {len(dropped)} for missing source) — failing slot, won't ship a "
            f"sparse remnant of a multi-cut concept ({dropped})")
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


def _drop_unavailable_av_cuts(manifests: dict, progress_cb: ProgressCb = None) -> None:
    """PD 2026-06-12 graceful degradation: BEFORE the AV preprocess step, make sure
    each cut's SOURCE photo is local — re-download on demand by UUID — and DROP any cut
    whose photo still can't be obtained. One missing/pruned photo used to fail the whole
    'Preprocessing photos' step (rc=1) and empty the slot (6/13 AV 18:00). Now the slot
    survives on its remaining cuts; only if too few remain do we give up. Updates
    sources/cuts/concept_cuts/captions/regen_prompts consistently."""
    try:
        sources_path = Path(manifests["sources"])
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
    except Exception:
        return
    cuts = manifests.get("cuts") or []
    con = _db()
    drop: list[str] = []
    for item in cuts:
        tag = item.get("tag")
        if not tag:
            continue
        entry = sources.get(tag)
        # AV stores sources[tag] as a STRING path; other paths use a dict. Handle both.
        if isinstance(entry, str):
            src, _is_str = entry, True
        elif isinstance(entry, dict):
            src, _is_str = (entry.get("source") or ""), False
        else:
            src, _is_str = "", False
        if src and not Path(src).is_absolute():
            src = str(ROOT / src)
        if src and Path(src).exists():
            continue
        uuid = entry.get("source_uuid") if isinstance(entry, dict) else None
        if not uuid:
            aid = (item.get("asset") or {}).get("asset_id") or item.get("asset_id")
            if aid:
                try:
                    r = con.execute("SELECT source_uuid FROM assets WHERE asset_id=?",
                                    (aid,)).fetchone()
                    uuid = r[0] if r else None
                except Exception:
                    uuid = None
        restored = _ensure_local(src, uuid) if src else None
        if restored and Path(restored).exists():
            if _is_str:
                sources[tag] = restored
            else:
                entry["source"] = restored
                sources[tag] = entry
            continue
        drop.append(tag)
    if not drop:
        return
    dropset = set(drop)
    manifests["cuts"] = [c for c in cuts if c.get("tag") not in dropset]
    for key in ("concept_cuts",):
        lst = manifests.get(key)
        if isinstance(lst, list):
            manifests[key] = [c for c in lst
                              if (c.get("tag") or c.get("cut_tag")) not in dropset]
    for t in drop:
        sources.pop(t, None)
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    # captions + regen_prompts files
    for mk in ("captions", "regen_prompts"):
        try:
            p = Path(manifests.get(mk, ""))
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                for t in drop:
                    d.pop(t, None)
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    remaining = len(manifests["cuts"])
    log.warning("AV: dropped %d cut(s) with unavailable photos: %s (%d remain)",
                len(drop), drop, remaining)
    if progress_cb:
        progress_cb(f":scissors: 자산 누락 {len(drop)}컷 드롭 → 남은 {remaining}컷으로 진행")
    if remaining < int(os.getenv("AV_MIN_CUTS", "3")):
        raise RuntimeError(
            f"too few cuts left after dropping unavailable photos ({remaining}) — "
            f"skip slot")


def _clip_has_human_face(mp4_path: Path, n_frames: int = 4) -> bool:
    """PD 2026-06-12 face GATE: VLM-check a rendered RF clip for a visible HUMAN FACE.
    The crop should have removed it, but if the crop window still includes a face this
    catches it. ONLY a face counts (hands/legs/back are fine). Fail-open (False) on no
    API / error — we never block on uncertainty, only act on a CLEAR face."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return False
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
                fp = Path(td) / f"f{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path), "-frames:v", "1",
                                str(fp)], check=False, timeout=20)
                if fp.exists() and fp.stat().st_size > 1000:
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return False
            parts.append(
                "Is a RECOGNIZABLE human face visible in ANY of these frames? "
                "RECOGNIZABLE = you could identify the person — i.e. the EYES (and "
                "usually the nose) are clearly visible. A hand, arm, leg, torso, back of "
                "the head, hair, or only a partial edge (just a chin/jaw, a cheek, the "
                "side of a face with NO eyes shown) is NOT recognizable — that is OK, "
                "answer false. Answer true ONLY when the eyes are visible enough to "
                "identify the person (a clear or near-clear face, incl. a mirror). "
                "Return ONLY JSON: {\"face_visible\": true|false}.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=parts,
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            import json as _json
            return bool(_json.loads((resp.text or "{}").strip()).get("face_visible"))
    except Exception as e:
        log.warning("face gate check failed (%s) — keeping", e)
        return False


def _rf_face_gate(manifests: dict, anim_dir: Path,
                  progress_cb: ProgressCb = None) -> None:
    """PD 2026-06-12 ("얼굴 부분을 crop해서 안보이게"): after trimming RF clips, verify NO
    human FACE survived the crop. For a cut whose render still shows a face, DROP it
    (a face must NEVER ship) — the crop is the primary guard, this is the safety net.
    Raises if too few cuts remain."""
    cuts = manifests.get("cuts") or []
    drop = []
    for item in cuts:
        tag = item.get("tag")
        mp4 = anim_dir / f"{tag}.mp4"
        if mp4.exists() and _clip_has_human_face(mp4):
            drop.append(tag)
            if progress_cb:
                progress_cb(f":no_entry: {tag} 사람 얼굴 감지 — 컷 드롭(절대 노출 금지)")
            log.warning("RF face gate: dropping %s (human face survived crop)", tag)
    if not drop:
        return
    dropset = set(drop)
    manifests["cuts"] = [c for c in cuts if c.get("tag") not in dropset]
    for key in ("concept_cuts",):
        lst = manifests.get(key)
        if isinstance(lst, list):
            manifests[key] = [c for c in lst
                              if (c.get("tag") or c.get("cut_tag")) not in dropset]
    for t in drop:
        try:
            (anim_dir / f"{t}.mp4").unlink(missing_ok=True)
        except Exception:
            pass
        for mk in ("captions",):
            try:
                p = Path(manifests.get(mk, ""))
                if p.exists():
                    d = json.loads(p.read_text(encoding="utf-8"))
                    d.pop(t, None)
                    p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            except Exception:
                pass
    if len(manifests["cuts"]) < int(os.getenv("RF_MIN_CUTS", "2")):
        raise RuntimeError(
            f"too few cuts left after dropping face-leaking cuts ({len(manifests['cuts'])})")


def run_real_footage_pipeline(manifests: dict, work_dir: Path,
                              progress_cb: ProgressCb = None,
                              dry_run: bool = False) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Step 0a: pre-render Seedance interp gap-fill cuts (legacy).
    _prerender_interp_fills(manifests, work_dir, progress_cb, dry_run)
    # Step 0b: pre-render Tier 2 photo cuts. PD 2026-06-13: real photos default to a
    # ken-burns still (no drift); only RF_PHOTO_MODE=i2v uses Seedance generation.
    _prerender_photo_kenburns_cuts(manifests, work_dir, progress_cb, dry_run)
    _prerender_photo_i2v_cuts(manifests, work_dir, progress_cb, dry_run)
    # Step 0c: pre-render Tier 3 chain-from-prev cuts (after 0a/0b resolved).
    _prerender_chain_from_prev(manifests, work_dir, progress_cb, dry_run)
    # Step 0d: pre-render split_horizontal / split_vertical cuts (PD 2026-06-03).
    _prerender_split_cuts(manifests, work_dir, progress_cb, dry_run)

    # Step 0e (#3): Editor agent — judge INTENT vs actual FOOTAGE, then set per-cut
    # technique/tempo/trim, reorder, drop, and flag any intent↔footage mismatch.
    # Runs BEFORE trim so its trim/drop/reorder shape the actual edit.
    anim_dir = work_dir / "animated"
    if not dry_run and os.getenv("EDITOR_AGENT", "1") != "0":
        manifests["style"] = "real_footage"
        _plan = _run_editor(manifests.get("concept") or manifests, manifests,
                            "real_footage", progress_cb)
        if _plan:
            _apply_edit_plan(manifests, _plan, anim_dir, progress_cb)
            manifests["_edit_plan"] = _plan  # carry mismatch for the upstream loop

    # Step 1: trim source clips into animated/ (PD 2026-06-02: split trim
    # from caption burn so VLM rewrite can run between them).
    _trim_real_footage_clips(manifests, anim_dir, progress_cb, dry_run)

    # Step 1a-face: HARD RULE face gate — drop any cut whose render still shows a human
    # FACE after the crop (PD 2026-06-12: a face leaked into a render).
    if not dry_run:
        _rf_face_gate(manifests, anim_dir, progress_cb)

    # Step 1a-continuity: trim a clip that pans OFF our pet for its tail (retro C14 #1 /
    # RF0800) — runs BEFORE captioning so the caption stages fit the trimmed clip.
    if not dry_run:
        try:
            _rf_subject_exit_tail_trim(work_dir, manifests, anim_dir, progress_cb)
        except Exception as ex:
            log.warning("subject-tail-trim skipped: %s", ex)

    # Step 1a-coherence: drop a cut fused from a different outing/activity with no
    # memory-lane through-line (retro C14 #2 / RF1800) — before captioning.
    if not dry_run:
        try:
            _rf_cross_cut_coherence_gate(manifests, anim_dir, progress_cb)
        except Exception as ex:
            log.warning("coherence-gate skipped: %s", ex)

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
        # PD 2026-06-13: don't run the full Caption-Agent rewrite (it clobbers upstream
        # single-pass fixes), but DO run the lighter GROUNDING GATE — it only corrects
        # cuts whose captions contradict the frame (subject absent / animal mis-ID /
        # blown-out), leaving matching captions untouched. Catches the corgi≠retriever /
        # Ryani-not-in-frame fabrications PD flagged.
        log.info("real_footage single-pass: action-grounded captions + grounding gate")
        # Layer 2 (PD 2026-07-06): regenerate captions FROM the clip's observed action arc,
        # anchored to when each beat happens (sniff→walk→squat=mark). Runs BEFORE the
        # grounding gate, which then verifies + the count-cap/여운 tail finish.
        try:
            _rf_action_grounded_captions(work_dir, manifests, anim_dir,
                                         progress_cb=progress_cb, dry_run=dry_run)
        except Exception as ex:
            log.warning("action-grounded captions skipped: %s", ex)
        if progress_cb:
            progress_cb(":lock: [1b/3] 단일-패스 캡션 보존 + 그라운딩 게이트")
        try:
            _rf_caption_grounding_gate(work_dir, manifests, anim_dir,
                                       progress_cb=progress_cb, dry_run=dry_run)
        except Exception as ex:
            log.warning("grounding gate skipped: %s", ex)
        # PD 2026-06-15: the missing wit stage — sharpen the grounded captions into 말맛
        # (RF singlepass otherwise skips AV's Caption-Agent/Polisher → flat 묘사체).
        try:
            _rf_caption_punchup(work_dir, manifests, anim_dir,
                                progress_cb=progress_cb, dry_run=dry_run)
        except Exception as ex:
            log.warning("caption punch-up skipped: %s", ex)
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

    # Step 1b-anchor: the caption regen above grounds on WHAT the footage shows and drops
    # the WHEN — deterministically restore the memory-lane time spine (opener/closer) that
    # the Writer set, before burn (retro C13). No-op unless multi-year memory-lane.
    _enforce_memorylane_anchors(manifests, anim_dir, progress_cb, dry_run)

    # Step 1c: burn captions on trimmed clips.
    manifests["style"] = "real_footage"  # enables caption reading-time fit (#4)
    # PD 2026-06-17: per-EPISODE captioned dir, NOT the shared data/output/
    # animated_captioned junk-drawer (cuts from every episode pile up there →
    # concurrent renders collide + a stale/other-episode cut can slip into the
    # assembly). Assemble reads from here via --in-dir below.
    captioned_dir = work_dir / "animated_captioned"
    _run(
        _burn_captions_cmd(manifests, anim_dir, captioned_dir),
        ":speech_balloon: [1c/3] Burning captions (post-VLM)",
        progress_cb, dry_run,
    )
    if not dry_run:
        _fade_out_ending(manifests, captioned_dir, progress_cb)  # 여운 f/o

    # Step 2: ensure bumpers exist
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            [sys.executable, "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            ":loud_sound: [2/3] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(":loud_sound: [2/3] Bumpers exist — skip")

    # Step 3: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_rf_{ts}.mp4"
    # PD 2026-06-11: persist render params for caption-salvage (re-caption these
    # rendered clips on a Giri caption-fail, no re-extract/re-render).
    _persist_render_meta(work_dir, manifests, manifests.get("cuts") or [],
                         (manifests.get("concept") or {}).get("cuts") or [],
                         style="real_footage")
    _run(
        [sys.executable, "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--in-dir", str(captioned_dir),
         "--intro-bumper", str(INTRO_BUMPER),
         "--outro-bumper", str(OUTRO_BUMPER),
         "--music", manifests.get("bgm", str(DEFAULT_BGM)),
         "--out", str(out)],
        ":clapper: [3/3] Final assembly",
        progress_cb, dry_run,
    )
    # PD 2026-06-11: a real_footage episode that came out far too short (e.g. the
    # 9.4s episode_rf_..._010216) = most cuts were DROPPED (failed asset download /
    # missing clip), even though the concept had 6 cuts × 7-8s. Don't publish a stub
    # — FAIL so the slot stays empty and self-heal/retry can rebuild it from the now
    # much-bigger pool. Floor via RF_MIN_SECONDS (default 14s incl. ~5s bumpers).
    if not dry_run and out.exists():
        try:
            _dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(out)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 0)
        except Exception:
            _dur = 0.0
        _min = float(os.getenv("RF_MIN_SECONDS", "14"))
        if 0 < _dur < _min:
            if progress_cb:
                progress_cb(f":x: RF 에피소드 너무 짧음 ({_dur:.1f}s < {_min:.0f}s) "
                            f"— 컷 드롭 의심, stub 공개 방지로 실패 처리")
            raise RuntimeError(
                f"real_footage too short ({_dur:.1f}s < {_min}s) — likely dropped "
                f"cuts; refusing to publish a stub")
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
            # PD 2026-06-13: include the cut's own scene direction (action) so the
            # image model doesn't invent the location (same fix as ai_vtuber path).
            _scene = item.get("action") or ""
            regen[item["tag"]] = (
                f"{cartoon_style}, featuring {subjects} in a {theme} scene. "
                f"{_scene}. {preserve}"
            )
        regen_path = work_dir / "regen_prompts.json"
        regen_path.write_text(json.dumps(regen, ensure_ascii=False, indent=2), encoding="utf-8")
        manifests["regen_prompts"] = str(regen_path)

    # Step 1: preprocess photos
    _run(
        [sys.executable, "scripts/preprocess_for_i2v.py",
         "--manifest", manifests["sources"],
         "--out-dir", str(input_dir)],
        ":gear: [1/6] Preprocessing photos",
        progress_cb, dry_run,
    )

    # Step 2: AI regen with cartoon style
    if progress_cb:
        progress_cb(":art: [2/6] AI 캐릭터 생성 시작...")
    # SCENE LOCK decision FIRST — it gates whether we pin a SINGLE scene-grounded ref.
    # A concept-ref places BOTH pets in ONE scene and pins EVERY cut's regen to it
    # (and the Seedance scene-ref anchors every cut to ONE empty room). That's perfect
    # for a single-space Short but FATAL for a multi-space concept: 083613's 6 distinct
    # rooms (창가→침실→부엌→소파) all collapsed to the SAME purple-cabinet two-shot, so
    # captions describing bed/couch/night had no matching footage (Giri caption 1/10).
    # The old code computed _lock_scene AFTER building concept_ref, so lock_scene=False
    # only relaxed generate_batch — the concept_ref still pinned every cut. Decide FIRST,
    # and for multi-location DON'T build the single scene-grounded ref at all; each cut
    # renders its OWN space from its own regen_prompt (character fidelity then rides on
    # the per-cut character refs, not a scene-locking establishing image).
    # AV_SCENE_LOCK = 1 (force lock) / 0 (force unlock) / auto (default).
    _lock_env = os.getenv("AV_SCENE_LOCK", "auto").lower()
    if _lock_env in ("1", "on", "true"):
        _lock_scene = True
    elif _lock_env in ("0", "off", "false"):
        _lock_scene = False
    else:
        _lock_scene = not _concept_is_multi_location(payload)
    if not _lock_scene and progress_cb:
        progress_cb(":world_map: 멀티장소 컨셉 — 단일 concept/scene ref 해제, 컷별로 자기 공간 생성")
    if not dry_run:
        from scripts.generate_character_scene import generate_batch
        # Single concept-grounded character ref pins every cut to ONE scene → ONLY when
        # the scene is locked (single-space). Multi-location → None (per-cut own space).
        concept_ref = None
        if _lock_scene and os.getenv("AV_CONCEPT_REF", "0") != "0":
            concept_ref = _build_concept_char_ref(payload, regen_dir, progress_cb)
        # Anchor every cut's IDENTITY to the clean real character reference so GPT can't
        # drift the pets' coat/markings — retro: AV0800 (2026-07-19) rendered Leo as a
        # grey/brown-and-white tabby instead of the ginger orange he is, because the
        # still-gen seeded cut1 from a candid pose photo (weak colour anchor) and the
        # style-anchor chain then propagated that drift to every cut. A clean front-facing
        # both-pets studio ref (base_both.png) holds Leo's orange + Ryani's markings far
        # better (validated: same prompt, base_both ref → correct orange Leo). Use it as a
        # CHARACTER LOCK — identity from the ref, scene/pose still from each cut's own
        # prompt — so it also works for multi-location episodes. Skipped when a
        # concept-grounded ref already exists. Gate: AV_CLEAN_CHAR_REF (default on).
        if concept_ref is None and os.getenv("AV_CLEAN_CHAR_REF", "1") != "0":
            _clean_ref = ROOT / "assets" / "character_ref" / "base_both.png"
            if not _clean_ref.exists():
                _clean_ref = ROOT / "assets" / "character_ref" / "base_both_clean.png"
            if _clean_ref.exists():
                concept_ref = _clean_ref
                _lock_scene = False  # character-lock (base_both is white-bg): scene per-cut
                if progress_cb:
                    progress_cb(":art: 캐릭터 앵커 = 실제 레퍼런스(base_both) — 색·무늬 드리프트 방지")
        failures = generate_batch(
            Path(manifests["regen_prompts"]),
            input_dir if input_dir.exists() else None,
            regen_dir,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            progress_cb=progress_cb,
            reference_override=concept_ref,
            lock_scene=_lock_scene,
            # PD 2026-06-17: pass concept + per-cut metadata so the best-of-N still
            # selector (REGEN_BEST_OF) judges candidates against this cut's intent.
            concept=payload,
            cuts_by_tag={c.get("tag"): c for c in (payload.get("cuts") or []) if c.get("tag")},
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
            [sys.executable, "scripts/animate_hero_veo3_vertex.py",
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
            [sys.executable, "scripts/build_bumpers.py",
             "--intro-music", str(BUMPER_MUSIC),
             "--outro-music", str(BUMPER_MUSIC)],
            ":loud_sound: [4/6] Building bumpers",
            progress_cb, dry_run,
        )
    elif progress_cb:
        progress_cb(":loud_sound: [4/6] Bumpers exist — skip")

    # Step 5: burn captions (손글씨 기본, Director font_override 가능)
    # PD 2026-06-17: per-EPISODE captioned dir (not the shared junk-drawer).
    captioned_dir = work_dir / "animated_captioned"
    _run(
        _burn_captions_cmd(manifests, anim_dir, captioned_dir),
        ":speech_balloon: [5/6] Burning captions",
        progress_cb, dry_run,
    )

    # Step 6: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_cs_{ts}.mp4"
    _run(
        [sys.executable, "scripts/assemble_episode.py",
         "--captions", manifests["captions"],
         "--in-dir", str(captioned_dir),
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
    ryani_marking_phrase = "white blaze"  # key phrase present in every Ryani marking block

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
                    "White markings on black face: a THIN narrow white blaze (a fine pencil-width line up the muzzle, between the eyes, to the forehead — NOT a wide splash) from nose to forehead, "
                    "a faint subtle eyebrow-like white mark above each eye (NOT a bold round dot). Silver-grey aged muzzle. "
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
            checks["marking"] = 1 if "white blaze" in prompt else 0
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
            has_blaze = "white blaze" in prompt
            if not has_blaze:
                log.warning("⚠ %s: Ryani scene WITHOUT 'white blaze' marking!", tag)
            log.info("%s: Ryani marking check: blaze=%s len=%d",
                     tag, has_blaze, len(prompt))

        cmd = [
            sys.executable, "scripts/animate_hero_veo3_vertex.py",
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
            [sys.executable, "scripts/build_bumpers.py",
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
        [sys.executable, "scripts/assemble_episode.py",
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


# Per-cut Seedance marking injection — central canon (agents/canon.py).
_RYANI_MARKING_EMPHASIS = canon.RYANI_MARKING
_LEO_MARKING_EMPHASIS = canon.LEO_MARKING


def _cut_character_ok(mp4_path: Path, who: str = "both", n_frames: int = 3,
                      strict_blaze: bool = False) -> bool:
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
            # PD 2026-06-09: text-only "is the blaze thin?" was too lenient — Seedance
            # ref-mode keeps over-widening Ryani's blaze and the gate passed it. Give
            # the VLM Ryani's CORRECT thin-blaze REFERENCE image to COMPARE against
            # (the same fix that made the scene gate reliable). Flag whenever the
            # rendered blaze is clearly WIDER than the reference, at ANY angle where
            # it's visible (not just frontal).
            blaze_ref = None
            n_render_blaze = len(parts)
            if ask_blaze:
                _bref = ROOT / "assets" / "character_ref" / "ryani_solo.png"
                if _bref.exists():
                    try:
                        parts.append(_gt.Part.from_bytes(
                            data=_bref.read_bytes(), mime_type="image/png"))
                        blaze_ref = True
                    except Exception:
                        blaze_ref = False
            blaze_q = (
                (f" The LAST image is the REFERENCE of Ryani's CORRECT markings — her "
                 f"white forehead BLAZE is a THIN NARROW line. The first "
                 f"{n_render_blaze} image(s) are the rendered cut. COMPARE the rendered "
                 f"Ryani's blaze to the reference. " +
                 ("Be STRICT (this is the final cut where drift peaks): set "
                  "blaze_too_thick=true if the rendered blaze is EVEN SOMEWHAT wider / "
                  "more spread-out than the reference's thin line, on any clear view of "
                  "her face. Only FALSE if it's as thin as the reference or her face "
                  "isn't clearly visible."
                  if strict_blaze else
                  "Set blaze_too_thick=true when the rendered blaze is NOTICEABLY wider "
                  "than the reference's thin line — a clearly thicker stripe or a broad "
                  "patch an attentive viewer would catch — judged on a FRONTAL/clear "
                  "view of her face. If her face is side/turned/unclear, or the blaze is "
                  "about as thin as the reference, set FALSE.")
                 if blaze_ref else
                 " SEPARATELY judge Ryani's white forehead BLAZE: correct = a THIN "
                 "NARROW line nose→forehead; DEFECT (blaze_too_thick=true) = clearly "
                 "THICK/WIDE/BROAD covering much of the forehead, at any angle where "
                 "visible; not-visible/thin = false.")
            ) if ask_blaze else ""
            blaze_field = ",\"blaze_too_thick\":true|false" if ask_blaze else ""
            # PD 2026-06-28 (삼계탕 재발): Seedance keeps hallucinating a white spot/patch on
            # Ryani's NAPE (back of neck) / spine — that is 삐용이's tuxedo marking bleeding
            # over; Ryani's nape/back/spine is PURE BLACK. The blaze check only looked at her
            # face, so nape-white sailed through render-time and only Giri caught it (forcing
            # a full re-render). Add a nape check against the SAME clean ryani_solo.png ref.
            ask_nape = w in ("ryani", "both")
            nape_q = (
                (" The reference (LAST image) shows Ryani's nape, the BACK of her neck, "
                 "spine and back as SOLID BLACK. Set nape_white=true if the rendered Ryani "
                 "has ANY white spot, dot, patch, stripe or line on the BACK of the neck "
                 "(nape), spine or back — that is wrong. Her FRONT-of-throat/chin/chest "
                 "white is CORRECT — do NOT set nape_white for that. If her back/nape isn't "
                 "clearly visible, set false."
                 if blaze_ref else
                 " SEPARATELY judge Ryani's NAPE: her nape (back of neck), spine and back "
                 "are PURE BLACK; nape_white=true if any white spot/dot/patch/stripe appears "
                 "on the back of the neck/spine/back (front-of-throat/chest white is correct "
                 "— do not flag it); not-visible = false.")
            ) if ask_nape else ""
            nape_field = ",\"nape_white\":true|false" if ask_nape else ""
            prompt = (
                "These frames are from ONE rendered cut. Judge the pet CHARACTER "
                "fidelity for: " + " ".join(specs) + " IMPORTANT: a side profile or "
                "turned-away face that naturally hides markings is FINE — not a "
                "defect. Flag a character problem ONLY when a pet is frontal/clearly "
                "visible AND its markings/features are clearly wrong, OR a pet looks "
                "obviously AI-distorted (warped face, melted/extra features, wrong "
                "proportions, plastic/fake look)." + blaze_q + nape_q +
                " Return ONLY JSON: {\"clear\":true|false,\"character_ok\":true|false"
                + blaze_field + nape_field + "}.")
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
            nape_bad = bool(d.get("nape_white")) if ask_nape else False
            bad = (clear and not ok) or blaze_bad or nape_bad
            if bad:
                why = ("nape-white(삐용이 마킹)" if nape_bad
                       else "BLAZE-too-thick" if blaze_bad else "drift/generative")
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
                " Decide TWO things: (1) obvious_defect = is the image GLITCHED / "
                "CORRUPTED in a way a competent artist would NEVER draw on purpose? "
                "(an object melted/warped/dissolving/smeared; the SAME furniture "
                "duplicated; an impossible/extra/merged/broken limb; a face or body "
                "fused into furniture; garbled anatomy). ⚠️ A scene that merely breaks "
                "REAL-WORLD PHYSICS but is CLEANLY and COHERENTLY drawn is NOT a defect "
                "— this channel intentionally uses surreal/fantasy gags (a pet SWIMMING "
                "indoors, a pet floating, water flooding a room and the cat surfing on "
                "it, indoor rain). Those are the HOOK, not bugs: obvious_defect=false "
                "for them. Only flag (1) when the render looks like a model GLITCH, not "
                "a deliberate fantasy. (2) intent_mismatch = compared to the "
                "reference/facts above, is "
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


# Dynamic actions Seedance often FAILS to render (falls back to a static shot) —
# the cut "서핑인데 서핑 안 함" class. We only run the (cost-bearing) action gate on
# cuts whose prompt PROMISES one of these, so static/gentle cuts are never re-gen'd.
_DYNAMIC_ACTION_KEYWORDS = (
    "surf", "surfing", "swim", "swimming", "paddle", "jump", "leap", "leaping",
    "jumping", "run", "running", "dash", "sprint", "dive", "diving", "splash",
    "splashing", "ride", "riding", "wave", "flooded", "underwater", "fly", "flying",
    "float", "floating", "spin", "spinning", "chase", "chasing", "pounce",
    "서핑", "수영", "헤엄", "점프", "뛰어", "달리", "물장구", "파도", "잠수", "날아", "떠",
)


def _cut_action_ok(mp4_path: Path, action_prompt: str, n_frames: int = 3) -> bool:
    """PD 2026-06-11 (렌더 바로 고치기): verify the cut actually RENDERED the dynamic
    action its prompt promised. Seedance frequently drops a hard/surreal action
    (cat surfing, dog swimming) and falls back to the pets just sitting on the floor
    — the caption then lies ("서핑 고양이" over a dry-floor shot). We ONLY run this on
    cuts whose prompt promises a dynamic action (keyword-gated, so static cuts cost
    nothing) and fail ONLY when the action is CLEARLY absent. Fail-open (keep) on
    uncertainty / no API / error."""
    pl = (action_prompt or "").lower()
    if not any(k in pl for k in _DYNAMIC_ACTION_KEYWORDS):
        return True  # no dynamic action promised → nothing to verify
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        import tempfile as _tf
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        dur = 5.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4_path)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 5.0)
        except Exception:
            pass
        parts = []
        with _tf.TemporaryDirectory() as td:
            for i in range(n_frames):
                t = dur * (i + 0.5) / n_frames
                fp = Path(td) / f"a{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path),
                                "-frames:v", "1", str(fp)], check=False, timeout=20)
                if fp.exists():
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return True
            prompt = (
                f"These are frames from a 5-second pet video cut. The cut was SUPPOSED "
                f"to depict this action: \"{action_prompt[:300]}\". Ignore camera, "
                f"lighting, and markings. Judge ONLY the pets' main ACTION: does the "
                f"intended dynamic action (e.g. surfing, swimming, jumping, running, "
                f"splashing, a flooded/water scene) actually appear, or are the pets "
                f"clearly just SITTING / STANDING / LYING STILL on a normal dry floor "
                f"with the action ABSENT? Answer action_absent=true ONLY if the key "
                f"dynamic action is unmistakably missing. When in doubt answer false "
                f"(we keep the cut). Return ONLY JSON: "
                f"{{\"action_absent\":true|false,\"note\":\"3-6 words\"}}.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=list(parts) + [prompt],
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            import json as _json
            d = _json.loads((resp.text or "{}").strip())
            absent = bool(d.get("action_absent"))
            if absent:
                log.info("cut action gate: action absent [%s] in %s",
                         d.get("note", ""), Path(mp4_path).name)
            return not absent
    except Exception as e:
        log.warning("cut action check failed (%s) — keeping cut", e)
        return True


# PD 2026-06-12: feeding concepts (츄르/관절약 튜브, 요거트 그릇, 손에 든 간식) where
# Seedance keeps dropping the held-treat action and renders the dog head-DOWN eating a
# crumb off the FLOOR instead. The VLM caption-rewrite then re-describes the floor-eating
# as if intended ("무한 집중"), so the wrong render shipped. This gate catches it BEFORE
# the rewrite can paper over it.
_FEEDING_KEYWORDS = (
    "tube", "튜브", "churu", "츄르", "lick", "핥", "treat", "간식", "관절", "영양제",
    "yogurt", "요거트", "그릭", "bowl", "그릇", "from the hand", "손에", "hand-held",
    "holding", "feeds", "feeding", "snack", "paste",
)


def _cut_feeding_ok(mp4_path: Path, action_prompt: str, n_frames: int = 3) -> bool:
    """Verify a HAND-HELD-treat feeding cut didn't degrade into floor-eating. Only runs
    when the prompt promises a held treat (tube/bowl/hand). Fail ONLY when the pet is
    clearly head-down eating off the FLOOR with no held treat. Fail-open on uncertainty."""
    pl = (action_prompt or "").lower()
    if not any(k.lower() in pl for k in _FEEDING_KEYWORDS):
        return True
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not Path(mp4_path).exists():
        return True
    try:
        from google import genai as _g
        from google.genai import types as _gt
        import tempfile as _tf
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        dur = 5.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4_path)],
                capture_output=True, text=True, timeout=15).stdout.strip() or 5.0)
        except Exception:
            pass
        parts = []
        with _tf.TemporaryDirectory() as td:
            for i in range(n_frames):
                t = dur * (i + 0.5) / n_frames
                fp = Path(td) / f"f{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", f"{t:.2f}", "-i", str(mp4_path),
                                "-frames:v", "1", str(fp)], check=False, timeout=20)
                if fp.exists():
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return True
            prompt = (
                f"Frames from a pet video cut. It was SUPPOSED to show a pet being fed a "
                f"treat HELD BY A HUMAN HAND — a long tube (churu / a paste tube) or a "
                f"bowl — and the pet licking THAT held treat. Intended: \"{action_prompt[:240]}\". "
                f"Look at the dog: is it instead clearly head-DOWN with its nose at the "
                f"FLOOR eating a crumb/kibble off the ground, with NO tube or bowl or hand "
                f"feeding it? Answer floor_eating=true ONLY if the dog is unmistakably "
                f"eating off the floor instead of from a held tube/bowl/hand. If a hand/"
                f"tube/bowl is present or you're unsure, answer false. Return ONLY JSON: "
                f"{{\"floor_eating\":true|false,\"note\":\"3-6 words\"}}.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=list(parts) + [prompt],
                config=_gt.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            import json as _json
            d = _json.loads((resp.text or "{}").strip())
            floor = bool(d.get("floor_eating"))
            if floor:
                log.info("cut feeding gate: floor-eating [%s] in %s",
                         d.get("note", ""), Path(mp4_path).name)
            return not floor
    except Exception as e:
        log.warning("cut feeding check failed (%s) — keeping cut", e)
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
                   manifests, tag, scene_ref_path=None, expected_facts: str = "",
                   strict_blaze: bool = False) -> bool:
    """PD 2026-06-08 per-cut self-heal, shared by i2v + ref dispatch. After a cut
    renders, run the angle-aware render gate (character markings + scene coherence +
    intent-vs-reference); on failure regenerate via the `regen(prompt_text)` callable
    ×3 with strengthened canon → 1 alt prompt → keep best effort. `scene_ref_path`/
    `expected_facts` enable the intent-match check. `strict_blaze` (PD 2026-06-09) =
    use the stricter blaze comparison — set for the LAST cut where chain-drift peaks
    and PD consistently catches a widened blaze."""
    def _ok():
        # Focused calls (character + scene + action) — bundling dilutes attention.
        # Returns None if OK, else the failing dimension so the heal can target it.
        if not _cut_character_ok(out_mp4, who, strict_blaze=strict_blaze):
            return "character"
        if not _cut_scene_ok(out_mp4, scene_ref_path=scene_ref_path,
                             expected_facts=expected_facts):
            return "scene"
        # PD 2026-06-11: did the promised DYNAMIC action actually render? (surf/swim
        # /jump). Keyword-gated inside, so static cuts cost nothing.
        if not _cut_action_ok(out_mp4, prompt):
            return "action"
        # PD 2026-06-12: a held-treat feeding cut must NOT degrade to floor-eating.
        if not _cut_feeding_ok(out_mp4, prompt):
            return "feeding"
        return None
    if not who or dry_run or not out_mp4.exists():
        return True
    reason = _ok()
    if reason is None:
        return True
    # PD 2026-06-10 COST: each regen is a full Seedance call. The old ×3 + alt =
    # up to 4 re-renders PER CUT (×6 cuts × episode-retry → the ~$100 runaway).
    # Default to ONE heal attempt; the alt-prompt extra render is opt-in. If still
    # not on-model, KEEP best-effort (advisory) — PD veto is the final net.
    _heal_tries = max(0, int(os.getenv("AV_GATE_HEAL_TRIES", "1")))
    resolved = False
    for r in range(_heal_tries):
        if progress_cb:
            progress_cb(f":repeat: {tag} {reason} 이상({who}) — 재생성 {r+1}/{_heal_tries}")
        # PD 2026-06-11: an ACTION failure (Seedance dropped the surf/swim/jump) needs
        # the ACTION re-emphasized, not just the marking canon — repeating the same
        # prompt re-drops it. Steer the regen hard at the missing motion.
        if reason == "action":
            heal_prompt = (
                prompt + " IMPORTANT: the animal must CLEARLY and fully PERFORM the "
                "described dynamic action (actually surfing on / swimming through / "
                "leaping into the water), the motion filling the frame — it must NOT "
                "be sitting, standing, or lying still on a dry floor. "
                # PD 2026-06-28: re-emphasizing the action made Seedance REGENERATE the
                # whole room (the 관찰왕 background-collapse: 하비 등장 컷이 다른 방으로
                # 튐). The room must be held pixel-identical — only the pet's body moves.
                "The room and background stay PIXEL-IDENTICAL to the input frame — do "
                "NOT regenerate, relocate, redraw, or re-light the room, furniture, "
                "walls, or window; ONLY the pet's body performs the action. " + emph)
        elif reason == "feeding":
            heal_prompt = (
                prompt + " IMPORTANT: a human HAND holds the treat (a long paste tube / "
                "bowl) up at the pet's mouth and the pet LICKS it directly from the held "
                "tube/bowl. The pet's head stays UP at the treat. There is NOTHING on the "
                "floor and the pet must NOT lower its head to eat off the ground. " + emph)
        else:
            heal_prompt = prompt + emph
        try:
            # PD 2026-06-11: pass the failing reason so the regen can pick the right
            # INPUT FRAME (action → chain anchor / prev last frame; marking → fresh
            # still). Back-compat: closures that don't accept it fall back to 1-arg.
            try:
                regen(heal_prompt, reason)
            except TypeError:
                regen(heal_prompt)
        except Exception as e:
            log.warning("regen %d failed for %s: %s", r + 1, tag, e); break
        reason = _ok()
        if reason is None:
            resolved = True; break
    if not resolved and os.getenv("AV_GATE_ALT_PROMPT", "0") == "1":
        if progress_cb:
            progress_cb(f":repeat: {tag} — 다른 프롬프트로 재도전")
        try:
            regen("Gentle natural motion, camera holds still." + emph)
            resolved = _ok()
        except Exception:
            pass
    if not resolved:
        # PD 2026-06-09: DON'T drop a chained cut on an unresolved marking — a missing
        # cut breaks the one-take chain → the episode fails Giri → the WHOLE concept
        # re-renders → infinite loop (78min on one slot, observed). KEEP the best-effort
        # cut (the last regen mp4 stays) + flag it for PD review/veto. A slightly-wide
        # blaze is far better than an endless re-render loop; PD's per-episode veto is
        # the final safety net. (Only a genuinely broken render should ever be dropped.)
        log.warning("av cut %s: %s unresolved after heal — KEEPING best effort "
                    "(no drop, avoid re-render loop); flag for PD review", tag, reason)
        if progress_cb:
            _label = {"action": "액션(서핑/수영 등) 렌더 실패",
                      "scene": "장면 이상", "character": "마킹"}.get(reason, reason)
            progress_cb(f":warning: {tag} {_label} — best effort로 유지 "
                        f"(루프 방지). PD 검수에서 별로면 veto")
        _key = "_action_imperfect_cuts" if reason == "action" else "_marking_imperfect_cuts"
        manifests.setdefault(_key, []).append(tag)
        return True
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


_LOCATION_BUCKETS = {
    "outdoor": ("plaza", "fountain", "beach", "park", "street", "outdoor", "garden",
                "sidewalk", "rooftop", "분수", "광장", "야외", "공원", "해변", "거리", "옥상"),
    "living":  ("living room", "livingroom", "sofa", "couch", "거실", "소파"),
    "bath":    ("bathroom", "sink", "bathtub", "basin", "washbasin", "toilet", "shower",
                "욕실", "세면대", "화장실", "욕조"),
    "kitchen": ("kitchen", "counter", "주방", "부엌"),
    "bedroom": ("bedroom", "bed ", "침실", "침대"),
    "cafe":    ("cafe", "café", "카페"),
}


def _concept_is_multi_location(payload: dict) -> bool:
    """PD 2026-06-13: True when the episode's cuts span 2+ distinct location buckets
    (e.g. 무더위: 분수광장 + 거실 + 욕실 세면대). Such concepts must NOT scene-lock to
    the first location — each cut keeps its own background. Scans per-cut action/beat
    text (where the writer/director states 'now inside a living room', etc.)."""
    buckets: set[str] = set()
    cuts = payload.get("cuts") or []
    for c in cuts:
        if not isinstance(c, dict):
            continue
        text = " ".join(str(c.get(k, "")) for k in
                        ("action", "beat", "scene", "location", "set", "background",
                         "set_description", "regen_prompt")).lower()
        for bucket, kws in _LOCATION_BUCKETS.items():
            if any(kw in text for kw in kws):
                buckets.add(bucket)
    return len(buckets) >= 2


def _concept_is_continuous_take(payload: dict) -> bool:
    """True ONLY for a single CONTINUOUS-MOMENT ai_vtuber concept — one unbroken take
    where each cut literally continues the previous cut's motion, so seeding each cut
    from the prior cut's last frame (chain mode) is correct.

    DEFAULT FALSE. Most AV concepts are multi-action MONTAGES — each cut is a distinct
    trick/beat, often a different subject (랴니 코→랴니 브이→레오 꼬리잡기→…). There the
    per-cut action-still IS the cut's content; auto-chaining discards that still and
    feeds Seedance the previous cut's last frame instead, so cut 1's drift (Seedance's
    push-in) cascades and EVERY later cut collapses into cut 1's ending — captions then
    match nothing (PD 2026-06-22: the 장기 대결 episode rendered as one sleepy-dog
    close-up across all cuts). For a montage, each cut must drive Seedance from its OWN
    still.

    Chaining is opt-IN: an explicit continuous-take flag on the concept AND a single
    consistent subject across the (non-wink) cuts. A subject change across cuts is, by
    itself, proof of a montage → never auto-chain. The wink_ending still chains via its
    own per-cut `chain_from_prev` (it is a true continuation of the last cut)."""
    if not payload:
        return False
    flag = str(payload.get("editing_concept") or payload.get("shot_structure")
               or payload.get("structure") or "").strip().lower()
    declared = (payload.get("continuous_take") is True
                or flag in {"one_take", "oner", "long_take", "single_take", "continuous"})
    if not declared:
        return False
    subs = {
        str(c.get("who") or c.get("subjects") or "").strip().lower()
        for c in (payload.get("cuts") or [])
        if isinstance(c, dict) and c.get("function") != "wink_ending"
    }
    subs.discard("")
    return len(subs) <= 1


def _pick_real_both_pets_seed() -> Path | None:
    """A REAL marking-correct photo of BOTH pets, with a local file. Seeding the
    concept reference from a real photo makes the model COPY the real markings
    (thin blaze, no tail, chartreuse eyes) instead of inventing them — the highest-
    leverage input for first-pass fidelity. None when no usable photo exists locally
    (icloud-pruned), so the caller falls back to the static official ref."""
    try:
        con = _db()
        rows = con.execute(
            "SELECT file_path FROM assets WHERE kind='photo' "
            "AND subjects_csv LIKE '%ryani%' AND subjects_csv LIKE '%leo%' "
            "AND quality_score >= 0.7 "
            "ORDER BY quality_score DESC, captured_iso DESC LIMIT 25"
        ).fetchall()
        for r in rows:
            p = Path(r[0])
            if p.exists() and p.stat().st_size > 10000:
                return p
    except Exception as e:
        log.warning("real both-pets seed lookup failed: %s", e)
    return None


def _build_concept_char_ref(payload: dict, regen_dir: Path,
                            progress_cb: ProgressCb = None) -> Path | None:
    """Generate a CONCEPT-GROUNDED character reference — Ryani+Leo placed in THIS
    episode's scene (payload.set_description). Every cut's regen is pinned to it, so
    it stops the scene drifting (beach→indoor mid-episode) AND it is the single seed
    whose fidelity propagates to all cuts. Because of that leverage we spend the
    selection budget HERE, not on every cut:
      • seed from a REAL both-pets photo (markings copied, not invented) — Lever 2;
      • generate AV_CONCEPT_REF_BEST_OF candidates and let still_select pick the
        marking-accurate one — Lever 1.
    A validated reference lets the per-cut REGEN_BEST_OF drop (cuts inherit a good
    seed instead of re-rolling 5× to dodge a bad one). Returns the locked ref path,
    or None to fall back to the old style-anchor chain. Gate: AV_CONCEPT_REF."""
    set_desc = (payload.get("set_description") or "").strip()
    if len(set_desc) < 30:
        return None
    try:
        from scripts.generate_character_scene import generate_scene, _get_reference_image
        # Lever 2: prefer a real both-pets photo as the markings seed.
        seed = None
        if os.getenv("AV_REF_REAL_SEED", "1") != "0":
            seed = _pick_real_both_pets_seed()
        if seed is None:
            seed = _get_reference_image("both", "S4")
        prompt = (
            "A casual photorealistic snapshot. Ryani (a small black French Bulldog — "
            "thin white muzzle blaze, white chest patch, white toes, NO tail, spayed "
            "female) AND Leo (an orange tabby cat — white chin tuft, yellow-green eyes) "
            "TOGETHER in this exact scene, both FULL BODY clearly visible, natural pose. "
            "Keep their EXACT markings from the reference image. "
            f"SCENE: {set_desc[:450]}"
        )
        regen_dir.mkdir(parents=True, exist_ok=True)
        # Lever 1: best-of-N reference candidates, judged for canon fidelity.
        best_of = max(1, int(os.getenv("AV_CONCEPT_REF_BEST_OF", "4")))
        cands: list[Path] = []
        for k in range(best_of):
            try:
                data = generate_scene(prompt, reference_image=seed)
            except Exception as e:
                log.warning("concept ref cand %d/%d failed: %s", k + 1, best_of, str(e)[:120])
                continue
            cp = regen_dir / f"_concept_char_ref_cand{k+1}.png"
            cp.write_bytes(data)
            cands.append(cp)
        if not cands:
            return None
        out = regen_dir / "_concept_char_ref.png"
        if len(cands) == 1:
            winner = cands[0]
        else:
            try:
                from agents import still_select
                ref_cut = {
                    "beat": "establishing reference still",
                    "subjects": "both",
                    "scene": set_desc[:300],
                    "regen_prompt": "both pets full body, exact canon markings, natural pose",
                }
                pick = still_select.pick_best_still(
                    cands, cut=ref_cut, concept=payload, lane="ai_vtuber")
                winner = Path(pick["winner_path"])
                if progress_cb:
                    progress_cb(f":dart: 컨셉 레퍼런스 best-of-{len(cands)} → #{pick['winner']} "
                                f"마킹-정확 1장 락: {(pick.get('reason') or '')[:50]}")
            except Exception as e:
                log.warning("concept ref select failed: %s — using first candidate", e)
                winner = cands[0]
        shutil.copy(winner, out)
        for cp in cands:  # keep only the locked winner
            if cp != winner and cp.exists():
                try:
                    cp.unlink()
                except Exception:
                    pass
        if progress_cb:
            _seed_kind = "실사진" if (seed and "assets/photos" in str(seed)) else "official"
            progress_cb(f":beach_with_umbrella: 컨셉 레퍼런스 락 완료 — 랴니·레오를 이 씬에 배치 "
                        f"({_seed_kind} 시드, 전 컷 ref로 고정)")
        return out
    except Exception as e:
        log.warning("concept char ref gen failed (fallback to chain): %s", e)
        return None


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

    # Step 0: graceful degradation — re-download or DROP cuts with missing photos so a
    # single unavailable asset doesn't fail the whole 'Preprocessing photos' step.
    if not dry_run:
        _drop_unavailable_av_cuts(manifests, progress_cb)
        cuts = manifests["cuts"]   # refresh after possible drops

    # Step 1: preprocess photos for i2v
    _run(
        [sys.executable, "scripts/preprocess_for_i2v.py",
         "--manifest", manifests["sources"],
         "--out-dir", str(input_dir)],
        ":gear: [1/6] Preprocessing photos",
        progress_cb, dry_run,
    )

    # Step 2: AI regen via GPT character generation
    if progress_cb:
        progress_cb(":art: [2/6] AI 캐릭터 생성 시작...")
    # SCENE LOCK decision FIRST — it gates whether we pin a SINGLE scene-grounded ref.
    # A concept-ref places BOTH pets in ONE scene and pins EVERY cut's regen to it
    # (and the Seedance scene-ref anchors every cut to ONE empty room). That's perfect
    # for a single-space Short but FATAL for a multi-space concept: 083613's 6 distinct
    # rooms (창가→침실→부엌→소파) all collapsed to the SAME purple-cabinet two-shot, so
    # captions describing bed/couch/night had no matching footage (Giri caption 1/10).
    # The old code computed _lock_scene AFTER building concept_ref, so lock_scene=False
    # only relaxed generate_batch — the concept_ref still pinned every cut. Decide FIRST,
    # and for multi-location DON'T build the single scene-grounded ref at all; each cut
    # renders its OWN space from its own regen_prompt (character fidelity then rides on
    # the per-cut character refs, not a scene-locking establishing image).
    # AV_SCENE_LOCK = 1 (force lock) / 0 (force unlock) / auto (default).
    _lock_env = os.getenv("AV_SCENE_LOCK", "auto").lower()
    if _lock_env in ("1", "on", "true"):
        _lock_scene = True
    elif _lock_env in ("0", "off", "false"):
        _lock_scene = False
    else:
        _lock_scene = not _concept_is_multi_location(payload)
    if not _lock_scene and progress_cb:
        progress_cb(":world_map: 멀티장소 컨셉 — 단일 concept/scene ref 해제, 컷별로 자기 공간 생성")

    # PRECISE STILL — Fix 2 (PD 2026-06-28). DEFAULT OFF since 2026-06-30 (AV_PRECISE_STILL=1
    # to re-enable). It composed cuts from the scene_ref + char refs via Gemini, but that flat
    # composite had NO depth → pets oversized/pasted-flat (원근 깨짐) and looked illustrated/2D,
    # and locking every cut to ONE AI concept_ref propagated that. PD: "원근 다 무시, 2d 느낌,
    # 파이프라인 망가졌어." Reverting to per-cut gpt-image stills → Seedance i2v restored
    # perspective + photorealism (PD accepts the background-shake tradeoff: "배경 흔들림은 어쩔
    # 수 없다"). AV_CONCEPT_REF likewise default OFF. Compose-then-skip is kept behind the flag.
    scene_ref_path: Path | None = None
    _precise_tags: set = set()
    _concept_cuts_e = manifests.get("concept_cuts", []) or []
    _concept_obj_e = manifests.get("concept", {}) or {}
    _av_precise = (os.getenv("AV_PRECISE_STILL", "0") == "1" and not dry_run and _lock_scene
                   and bool(os.environ.get("BYTEPLUS_API_KEY", ""))
                   and (card.get("render_style") or "").lower() == "ai_vtuber")
    if _av_precise:
        _set_desc_e = (_concept_obj_e.get("set_description") or "").strip()
        if not _set_desc_e and _concept_cuts_e and _concept_cuts_e[0].get("space"):
            _set_desc_e = f"Scene takes place in {_concept_cuts_e[0]['space']}."
        scene_ref_path = _resolve_scene_ref(_concept_obj_e.get("set_anchor"), _set_desc_e,
                                            work_dir / "scene_ref.png", dry_run=dry_run)
        if scene_ref_path:
            regen_dir.mkdir(parents=True, exist_ok=True)
            if progress_cb:
                progress_cb(f":white_check_mark: scene_ref ready → {scene_ref_path.name}")
            for i, item in enumerate(cuts):
                cc = _concept_cuts_e[i] if i < len(_concept_cuts_e) else {}
                tag = item["tag"]
                if "wink" in (tag or "").lower() or "wink" in (cc.get("beat") or "").lower() \
                        or _cut_is_fantasy(cc):
                    continue
                refs = _cut_char_refs(cc)
                if not refs:
                    continue
                if _compose_av_still(scene_ref_path, refs, _av_still_compose_prompt(cc),
                                     regen_dir / f"{tag}.png"):
                    _precise_tags.add(tag)
            if progress_cb:
                progress_cb(f":dart: 정밀 스틸 {len(_precise_tags)}컷 합성 (gpt-image 생략, i2v 충실)")
            log.info("AV_PRECISE_STILL: pre-composed %d still(s) from %s; skipped in generate_batch",
                     len(_precise_tags), scene_ref_path.name)

    if not dry_run:
        from scripts.generate_character_scene import generate_batch
        # Single concept-grounded character ref pins every cut to ONE scene → ONLY when
        # the scene is locked (single-space). Multi-location → None (per-cut own space).
        concept_ref = None
        if _lock_scene and os.getenv("AV_CONCEPT_REF", "0") != "0":
            concept_ref = _build_concept_char_ref(payload, regen_dir, progress_cb)
        # Precise-composed cuts are excluded from the (slow gpt-image) generate_batch.
        _regen_manifest = Path(manifests["regen_prompts"])
        if _precise_tags:
            _full = json.loads(_regen_manifest.read_text())
            _rest = {k: v for k, v in _full.items() if k.startswith("_") or k not in _precise_tags}
            if any(not k.startswith("_") for k in _rest):
                _regen_manifest = work_dir / "regen_prompts_rest.json"
                _regen_manifest.write_text(json.dumps(_rest, ensure_ascii=False), encoding="utf-8")
            else:
                _regen_manifest = None  # every cut precise-composed → nothing left for gpt-image
        if _regen_manifest is not None:
            failures = generate_batch(
                _regen_manifest,
                input_dir if input_dir.exists() else None,
                regen_dir,
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                progress_cb=progress_cb,
                reference_override=concept_ref,
                lock_scene=_lock_scene,
                # PD 2026-06-17: pass concept + per-cut metadata so the best-of-N still
                # selector (REGEN_BEST_OF) judges candidates against this cut's intent.
                concept=payload,
                cuts_by_tag={c.get("tag"): c for c in (payload.get("cuts") or []) if c.get("tag")},
            )
            if failures:
                total = len([k for k in json.loads(_regen_manifest.read_text()).keys() if not k.startswith("_")])
                success = total - failures
                min_required = max(4, int(total * 0.75)) if not _precise_tags else max(1, int(total * 0.5))
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
    # Single empty-room scene_ref anchors EVERY Seedance cut to ONE room — only when the
    # scene is locked (single-space). Multi-location → None, so each cut keeps the space
    # its own still already established (else the lone home_livingroom ref drags every
    # cut back to the living room, re-collapsing the journey). NOTE: when AV_PRECISE_STILL
    # already resolved scene_ref above, reuse it (don't re-resolve).
    set_anchor = concept_obj.get("set_anchor")  # always defined (used later in dispatch)
    if use_seedance and _lock_scene and scene_ref_path is None:
        fallback_path = work_dir / "scene_ref.png"
        if progress_cb and set_anchor:
            progress_cb(f":frame_with_picture: scene_ref resolving (set_anchor={set_anchor})")
        scene_ref_path = _resolve_scene_ref(
            set_anchor, set_description, fallback_path, dry_run=dry_run,
        )
        if scene_ref_path and progress_cb:
            origin = "library" if "scene_refs" in str(scene_ref_path) else "fallback (GPT)"
            progress_cb(f":white_check_mark: scene_ref ready ({origin}) → {scene_ref_path.name}")

    # (Precise stills + _precise_tags were composed earlier, before generate_batch.)
    for i, item in enumerate(cuts):
        tag = item["tag"]
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        mode = cc.get("seedance_mode", "i2v")
        # Fix 2: a cut whose still we recomposed from the scene_ref → drive it via i2v from
        # that precise still (override the Director's ref default; the still carries the room).
        if tag in _precise_tags:
            mode = "i2v"
            cc["seedance_mode"] = "i2v"
        # PD 2026-06-28 (관찰왕 vs 매복러 "하비 등장" 컷 배경 붕괴): in a locked single-space
        # AV episode, an in-space cut MUST stay in `ref` mode. `i2v` drops the scene_ref
        # background anchor (BytePlus can't mix first_frame + reference_*), so any cut whose
        # prompt says "문이 열리는 순간 / 누군가 들어온다 / 하비 등장" makes Seedance regenerate the
        # whole room from scratch → 배경 붕괴. A human entrant is the worst case: humans have
        # NO character ref, so without the scene anchor the entire frame is unconstrained.
        # ref mode renders the same action (jump/pounce refs stay in `references[]`) while
        # keeping the room. Coerce only real in-room cuts — never the wink close-up
        # (space=None, deliberate i2v continuation) or a fantasy/dreamscape beat.
        _is_av = (card.get("render_style") or "").lower() == "ai_vtuber"
        _in_locked_space = bool(cc.get("space")) and "wink" not in (tag or "").lower() \
            and "wink" not in (cc.get("beat") or "").lower()
        # A cut deliberately anchored to a real photo (first_frame_asset_id) keeps its i2v —
        # that still IS its background anchor and the real-photo grounding reduces AI-look.
        # AV_PRECISE_STILL (Fix 2): the still was recomposed from the clean scene_ref, so it
        # already carries the room — KEEP i2v (it animates that faithful frame). The i2v→ref
        # coercion below exists only for the OLD path where i2v had no room-anchored still.
        if (_is_av and _lock_scene and scene_ref_path and mode == "i2v"
                and _in_locked_space and not cc.get("first_frame_asset_id")
                and not _cut_is_fantasy(cc) and not _av_precise):
            mode = "ref"
            cc["seedance_mode"] = "ref"
            if progress_cb:
                progress_cb(f":lock: {tag} 단일공간 락 컷 — i2v→ref 강제(배경 앵커 유지, 붕괴 방지)")
            log.info("seedance_mode coerced i2v→ref for locked-space AV cut %s "
                     "(scene_ref anchor preserved)", tag)
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
        # ★GROUND EACH CUT TO ITS OWN SITUATION/SPACE (PD 2026-07-04, AV21 v2 cut3 flicker):
        # the reality set — its set_description prefix + scene_ref image + omni room photos —
        # belongs ONLY to cuts that actually take place IN that locked room. Blanket-applying
        # it to a cut whose situation is a DIFFERENT space (a fantasy dreamscape, or another
        # location) injects the wrong room and makes Seedance oscillate between the two (cut3's
        # flower path kept flickering back to the empty living room). A cut is "in the reality
        # set" when it is NOT a fantasy beat AND its space matches the episode's set anchor (or
        # it declares no distinct space, e.g. the wink close-up). Cuts in another space take
        # their scene from their OWN prompt; character refs still anchor identity below.
        _cut_space = str(cc.get("space") or cc.get("set_anchor") or "").strip().lower()
        _ep_set = str(set_anchor or "").strip().lower()
        _in_reality_set = (not _cut_is_fantasy(cc)) and (
            not _cut_space or not _ep_set or _cut_space == _ep_set)
        # ★WINK CLOSE-UP is exempt too (PD 2026-07-04, AV18 '햅삐에 배경이 생성돼'): the wink cut is
        # an i2v tight two-shot animated FROM its own still — that still IS the background anchor.
        # Prepending the full room description makes Seedance drift the cozy close-up INTO the
        # described living room mid-clip (the couch/rug morphs into bench+piano+console+window).
        # Its own still + a hold-background prompt is enough; the room text only fights it.
        _is_wink = ("wink" in (tag or "").lower()
                    or (cc.get("beat") or "").lower() == "wink_ending"
                    or (cc.get("function") or "").lower() == "wink_ending")
        # Prepend the verbatim set_description ONLY for cuts in that reality set (the Director's
        # per-cut motion_prompt carries the character action; the room comes from the anchor).
        if set_description and _in_reality_set and not _is_wink \
                and not cut_prompt.startswith(set_description[:40]):
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

        # Sanctioned costume (PD 2026-06-30): when an episode's whole premise IS an
        # outfit (e.g. 우비 패션쇼 = raincoat fashion show), the garment is the payoff,
        # not anthropomorphization — so the bare-furred default must NOT strip it.
        # The Director sets a concept-level `costume_prop` {"wearer","item"}; we inject
        # that item on every cut where the wearer appears and keep the OTHER pet (and
        # the rest of the wearer's coverage) bare-furred. Sourced from the concept/cut
        # manifest like set_anchor (it is per-episode, not per-set), and — unlike
        # requires_harness — it is NOT killed for ai_vtuber (a costume concept IS AV).
        costume = _resolve_costume_for_cut(cc, manifests)

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
        # PD 2026-06-13: a FANTASY ai_vtuber scene (beach 판타지 등) is NOT a real
        # outing — it doesn't need the outdoor-etiquette harness, and forcing one
        # CONFLICTS with the harness-free base ref → the model invents inconsistent
        # harnesses mid-episode. So skip the harness injection for ai_vtuber by
        # default (the clean base has none). real_footage (real walks/cafe) keeps it.
        # A separate harness-version base + AV_FORCE_HARNESS=1 covers AV daily concepts.
        _rstyle = (card.get("render_style") or "").lower()
        if requires_harness and _rstyle == "ai_vtuber" \
                and os.getenv("AV_FORCE_HARNESS", "0") != "1":
            requires_harness = False
        if costume:
            costume_inject = _costume_inject_text(costume)
            if costume_inject not in prompt:
                prompt = prompt + " " + costume_inject
        elif requires_harness:
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

        # PD 2026-06-24: realism guards (static background, lo-fi look, single-room spatial
        # lock) are for REALITY cuts. An imagination/fantasy beat needs the opposite — a
        # living, vivid dreamscape — so skip those guards and use the vivid directive instead.
        _fantasy = _cut_is_fantasy(cc)

        # Background stillness guardrail (Seedance freelances animation on bg objects —
        # pots, books, plants spontaneously move). Lock everything except the pets — but
        # NOT in a fantasy cut, where the world is meant to come alive.
        bg_still = (
            "Background objects (plants in pots, books, decor, picture frames, "
            "furniture, lamps, dishes) are completely static throughout — "
            "ONLY the named pets and explicitly mentioned hands move. No "
            "shaking, drifting, swaying, or floating of stationary objects."
        )
        if not _fantasy and bg_still not in prompt:
            prompt = prompt + " " + bg_still

        # Look: lo-fi real-phone for reality cuts (so AV blends with real_footage);
        # vivid wondrous dreamscape for imagination/fantasy cuts.
        if _fantasy:
            if "VIVID DREAMSCAPE" not in prompt:
                prompt = prompt + " " + VIVID_FANTASY_DIRECTIVE
        elif os.getenv("AV_LOFI", "1") != "0" and "LO-FI RESOLUTION" not in prompt:
            prompt = prompt + " " + LOFI_REALISM_DIRECTIVE
        # PD 2026-06-14: motion must ALWAYS be plentiful — lively, dynamic characters. Only
        # genuine rest/sleep beats are calm. Push the i2v toward active, energetic movement.
        if os.getenv("AV_MOTION_EMPHASIS", "1") != "0" and "MOTION:" not in prompt:
            prompt = prompt + (
                " MOTION: the pets move with LIVELY, ENERGETIC, dynamic real motion — clearly "
                "active and full of life the whole time (believable continuous movement, never "
                "stiff, frozen, or barely-moving). Maximize natural movement for the action; "
                "ONLY an explicit rest/sleep beat should be calm.")

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

        # Spatial anchor lock — pin the room's furniture positions across cuts so the
        # background doesn't drift. Skip on fantasy cuts: a dreamscape is a NEW world, not
        # the locked living room, so anchoring it to home furniture would fight the fantasy.
        try:
            if sa_for_anti and lib_data and not _fantasy and not _is_wink:  # wink holds its still, not the room furniture
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
                "Ryani's markings (CONSISTENT EVERY CUT): THIN narrow "
                "white blaze (a fine pencil-width line up the muzzle, between the "
                "eyes, to the forehead — NOT the typical "
                "wide splash) from nose to forehead, a faint subtle eyebrow-like "
                "white mark above each eye (NOT a bold round dot), "
                "silver-grey aged muzzle, white chin, large white "
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
                sys.executable, "scripts/animate_hero_veo3_vertex.py",
                "--image", str(regen_png),
                "--prompt", prompt,
                "--seconds", "4",
                "--model", os.getenv("VEO_MODEL", "veo-3.0-generate-001"),
                "--output", str(out_mp4),
            ]
            _run(cmd, f":film_frames: [3/6] Veo i2v {tag}", progress_cb, dry_run)
            continue

        seconds_int = int(cc.get("duration_seconds", 5))
        # NOTE (PD 2026-06-10): the fast Seedance model (dreamina-seedance-2-0-fast)
        # ONLY accepts 5s for BOTH ref and i2v — it rejects duration=3/4 with HTTP 400
        # "duration not valid for ... in i2v" (verified). So per-cut RENDER length can't
        # be shortened on the fast model; EPISODE length is controlled by POST-TRIMMING
        # each rendered 5s cut to AV_CUT_OUTPUT_SECONDS in Step 3 (render stays 5s).
        # Fast model + ref mode = 5s hard cap. Clamp to avoid Ark API error.
        model_in_use = os.getenv("SEEDANCE_MODEL", DEFAULT_MODEL_SEEDANCE)
        if "fast" in model_in_use and mode == "ref" and seconds_int > FAST_MODEL_REF_MAX_SECONDS:
            log.info("clamping %s duration %ds → %ds (fast model + ref cap)",
                     tag, seconds_int, FAST_MODEL_REF_MAX_SECONDS)
            seconds_int = FAST_MODEL_REF_MAX_SECONDS
        seconds = str(seconds_int)

        if mode == "ref":
            # PD 2026-06-10 ROOT FIX (마킹): the "pair" ref image
            # (official_ryani_leo.png) is WRONG — Ryani has NO white blaze and both
            # pets wear hanbok — so a cut generated against it can NEVER reproduce
            # Ryani's thin blaze, and the marking gate (which judges vs the CORRECT
            # ryani_solo.png) fails forever → endless costly re-renders. Anchor
            # generation to the CORRECT solo refs (matching the gate) so markings
            # render right the FIRST time. Pick by who's in the cut; substitute any
            # explicit "pair" too.
            ref_names = cc.get("references")
            if not ref_names or ref_names == ["pair"]:
                _who = (cc.get("who") or "both").lower()
                ref_names = (["ryani_solo"] if _who == "ryani"
                             else ["leo_solo"] if _who == "leo"
                             else ["ryani_solo", "leo_solo"])
            else:
                _seen: set = set()
                ref_names = [r for n in ref_names
                             for r in (["ryani_solo", "leo_solo"] if n == "pair" else [n])
                             if not (r in _seen or _seen.add(r))]
            # PD 2026-06-11 (b) / 2026-07-01: age-aware Ryani reference. A cut framed
            # as PAST / young Ryani (memory-lane "9년전", "어린/아기", years_ago≥7) uses
            # the YOUNG reference (black face, NO grey muzzle); a present-day cut keeps
            # the senior ryani_solo.
            # ★DECOUPLE THE CUE-SHEET FROM THE CAPTION (PD 2026-07-01, 봉준호 콘티 원칙):
            # the visual we render is driven by the PRODUCTION cue-sheet (the Director's
            # shot/era fields), NOT by the caption text. Captions are a SEPARATE narration
            # layer laid on top and are NOT 1:1 with the picture. Reading the caption to
            # pick a reference is the bug that made a present-day "나 2015년생인데~" self-
            # intro pull the 2015 PUPPY ref (the year in the caption was misread as 2015-
            # era footage). So: scan ONLY visual/era fields below — never `captions`.
            _era = str(cc.get("subject_era") or "").strip()      # cue-sheet life-era (canon, asset-dated)
            _blob = " ".join(str(cc.get(k, "")) for k in
                             ("motion_prompt", "regen_prompt", "beat", "function",
                              "action", "description", "subject_era", "subject_era_label"))
            _ya = cc.get("years_ago")                            # cue-sheet field (asset captured date)
            _m = re.search(r"(\d+)\s*년\s*전", _blob)
            _years_back = (int(_m.group(1)) if _m
                           else (int(_ya) if isinstance(_ya, (int, float)) and _ya else 0))
            # young ⟺ the CUE-SHEET says past-puppy RYANI. The swap only ever replaces
            # ryani_solo → ryani_young, so the puppy signal must be about RYANI, never Leo.
            # ★PET-SCOPE the loose keyword/year match (PD 2026-07-01): a present-day TWO-SHOT
            # whose prose says "아기 레오"(baby cat) must NOT puppify the senior Ryani beside
            # him. So the ambiguous 아기/강아지/footage-year words only count on a RYANI-SOLO
            # cut; for both-pet or Leo cuts, rely on the unambiguous structural signals —
            # a Ryani-stamped young subject_era, or a genuine 7+yr footage gap. The Director's
            # prose may name a footage year (2015년 겨울 …) but a stated birth-year (2015년생,
            # present age) is excluded — it is NOT a footage-era signal.
            _who = (cc.get("who") or "").lower()
            _ryani_solo_cut = ("ryani" in _who and "leo" not in _who)
            _era_label = str(cc.get("subject_era_label") or "")
            _young = (
                (_era in ("아기", "어린") and "랴니" in _era_label)   # cue-sheet: RYANI's young era
                or _years_back >= 7                                  # genuine memory-lane gap
                or (_ryani_solo_cut and (
                    bool(re.search(r"아기|강아지|새끼|퍼피|puppy", _blob, re.IGNORECASE))
                    or bool(re.search(r"(?<!\d)(2015|2016|2017)(?!\s*년?\s*생)", _blob))))
            )
            if _young:
                ref_names = ["ryani_young" if n == "ryani_solo" else n for n in ref_names]
                if progress_cb:
                    progress_cb(f":baby: {tag} 강아지시절 랴니 컷 — young 레퍼런스 사용")
            ref_paths = [_resolve_ref(n) for n in ref_names]
            ref_paths = [p for p in ref_paths if p is not None]
            if not ref_paths:
                log.warning("ref mode %s: no resolved refs, falling back to i2v", tag)
                mode = "i2v"
            else:
                # Add scene_ref as ADDITIONAL anchor (BytePlus allows up to 9 refs).
                # Character refs anchor identity; scene ref anchors the room — but ONLY for a
                # cut whose situation IS that reality room. A fantasy/other-space cut must NOT
                # carry the reality scene_ref image or the empty living room bleeds into the
                # dreamscape (AV21 v2 cut3 flicker, PD 2026-07-04). Same _in_reality_set gate
                # as the set_description prefix above — grounding follows the cut's own space.
                full_refs = list(ref_paths)
                if scene_ref_path and scene_ref_path.exists() and _in_reality_set:
                    full_refs.append(scene_ref_path)
                # Omni reference (2026-05-31, PD request): pull `scene_ref_extras`
                # from set_library — extra PD-real-photos of the same room from
                # different POVs. Seedance learns the room from multi-photo
                # evidence instead of one scene_ref + text description. Same situation gate:
                # these are REALITY-room photos, so skip them on a fantasy/other-space cut
                # (they bleed the living room into the dreamscape — AV21 v2 cut3, PD 2026-07-04).
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
                    if set_anchor and _in_reality_set:
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
                # PD 2026-07-04: inject the REAL photo of any named prop this cut mentions
                # (곶감꼭지 등) as a Seedance reference. object_refs is otherwise text-only, and
                # text can't teach Seedance a prop's shape (image beats text — same as the A7
                # character-ref lesson). 하비 uploaded real 곶감꼭지 photos to the grandma channel
                # BECAUSE AV kept inventing the wrong object; feeding that photo here is what
                # actually makes the render match the real thing. Bounded by the 9-ref cap.
                try:
                    # PD 2026-07-20: `cc` here is the ASSET-ALIGNED cut, which can be stripped of
                    # the Director's Korean `description` (where prop names 하네스/가방 live) — so the
                    # prop-ref auto-detect silently found nothing and the harness/bag drifted (AV
                    # X5Le). Also scan the ORIGINAL concept cut (kept intact in manifests["concept"],
                    # matched by tag) so a prop named only in the description is still detected.
                    # Resolve the ORIGINAL cut (with the Korean description where prop names live)
                    # straight from the DB card payload — the asset-aligned `cc` here is stripped of
                    # descriptions. _load_card returns dict(row) so payload_json is always present;
                    # dict(card) normalizes a sqlite.Row too. Bulletproof source of truth.
                    _orig_cut = {}
                    _dbg = ""
                    try:
                        import json as _json
                        _cardd = dict(card) if card is not None else {}
                        _pl = _json.loads(_cardd.get("payload_json") or "{}")
                        _pcuts = _pl.get("cuts") or (_pl.get("concept") or {}).get("cuts") or []
                        _orig_cut = next((oc for oc in _pcuts if oc.get("tag") == tag),
                                         _pcuts[i] if i < len(_pcuts) else {}) or {}
                        _dbg = ("cardtype=%s pcuts=%d desc=%d"
                                % (type(card).__name__, len(_pcuts),
                                   len(str(_orig_cut.get("description") or ""))))
                    except Exception as _pe:
                        _dbg = "ERR %r" % (_pe,)
                        _orig_cut = {}
                    _cut_text = ((prompt or "") + " " + str(cc.get("motion_prompt") or "")
                                 + " " + str(cc.get("description") or "")
                                 + " " + str(_orig_cut.get("description") or "")).lower()
                    if progress_cb:
                        progress_cb("PROPDBG %s %s har=%s bag=%s"
                                    % (tag, _dbg, "하네스" in _cut_text, "가방" in _cut_text))
                    with _db() as _con:
                        _props = _con.execute(
                            "SELECT name, file_path FROM object_refs "
                            "WHERE file_path IS NOT NULL AND TRIM(name)!=''").fetchall()
                    if progress_cb:
                        progress_cb("PROPDBG2 %s nprops=%d" % (tag, len(_props)))
                    for _nm, _pth in _props:
                        if len(full_refs) >= 9:
                            break
                        if _nm and _nm.strip().lower() in _cut_text:
                            _pp = ROOT / _pth if not Path(_pth).is_absolute() else Path(_pth)
                            if progress_cb:
                                progress_cb("PROPMATCH %s nm=%s exists=%s dup=%s pp=%s"
                                            % (tag, _nm, _pp.exists(), _pp in full_refs, _pp))
                            if _pp.exists() and _pp not in full_refs:
                                full_refs.append(_pp)
                                if progress_cb:
                                    progress_cb("PROPADDED %s %s -> %d refs" % (tag, _pp.name, len(full_refs)))
                except Exception as ex:
                    log.warning("prop ref load failed: %s", ex)
                    if progress_cb:
                        progress_cb("PROPERR %s %r" % (tag, ex))
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
                        sys.executable, "scripts/animate_seedance_i2v.py",
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
                               expected_facts=_set_expected_facts(set_anchor),
                               strict_blaze=(i >= len(cuts) - 2))
                continue

        if mode == "interp":
            # Rare path for ai_vtuber: explicit interp between two stills.
            # If Director didn't supply both stills, fall back to i2v.
            anchors = cc.get("fill_anchors") or {}
            first_p = anchors.get("first_frame_path")
            last_p = anchors.get("last_frame_path")
            if first_p and last_p and Path(first_p).exists() and Path(last_p).exists():
                cmd = [
                    sys.executable, "scripts/animate_seedance_i2v.py",
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
        # Auto-chain is opt-IN, for a CONTINUOUS-TAKE concept only (PD 2026-06-22).
        # History: 2026-06-13 this auto-chained EVERY single-location AV concept to keep
        # the background pixel-continuous (cushions/pillows had drifted across fresh
        # regens). But single-LOCATION ≠ single-MOMENT: a 거실 "각자 장기 대결" is one room
        # yet a MONTAGE — each cut a different trick/subject. Chaining there discarded
        # each cut's own action-still and fed Seedance the previous cut's last frame, so
        # cut 1's push-in drift cascaded and every cut collapsed into one sleepy-dog
        # close-up — all captions then matched nothing. Fix: chain ONLY when the concept
        # is a declared continuous take with a single consistent subject; otherwise each
        # cut drives Seedance from ITS OWN still (background stays consistent anyway via
        # the validated concept_ref + scene-lock seed). Per-cut chain_from_prev (e.g. the
        # wink) is always honored. Global kill: AV_CHAIN_CUTS=0.
        _av_chain_on = (
            (card.get("render_style") or "").lower() == "ai_vtuber"
            and os.getenv("AV_CHAIN_CUTS", "1") != "0"
            and not _concept_is_multi_location(payload)
            and _concept_is_continuous_take(payload)
        )
        if (cc.get("chain_from_prev") or _av_chain_on) and i > 0:
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
                sys.executable, "scripts/animate_seedance_i2v.py",
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

        def _i2v_regen(p: str, reason: str = None):
            # PD 2026-06-11: pick the regen INPUT FRAME by failure reason — same as
            # the surgical single-cut re-gen, but inside the chain code.
            #  - ACTION failure on a chained cut → re-gen from the PREVIOUS cut's last
            #    frame (the chain anchor = first_frame_path) so the scene continuity
            #    (e.g. the flooded room) carries and the dynamic action can render.
            #    Re-genning from the fresh DRY still drops the water → the surf fails
            #    again (the exact #1 cut4 bug).
            #  - marking drift → re-gen from the fresh canonical still (clean blaze).
            if reason == "action" and cc.get("chain_from_prev"):
                if progress_cb:
                    progress_cb(f":ocean: {tag} 액션 재생성 → 이전 컷 last frame(체인 앵커)에서")
                _seedance_i2v_safe(p, image=first_frame_path)
            else:
                if _regen_img and progress_cb:
                    progress_cb(f":arrows_counterclockwise: {tag} 체인 드리프트 → 원본 스틸로 재생성")
                _seedance_i2v_safe(p, image=_regen_img)

        _i2v_sa = cc.get("set_anchor") or set_anchor
        # PD 2026-06-09: chain drift peaks at the FINAL cut → stricter blaze there
        # (PD repeatedly catches a widened blaze on the last cut). Limited to one cut
        # + keep-best-effort, so no global re-render loop.
        _is_last_region = (i >= len(cuts) - 2)  # last 2 cuts: drift peaks
        _gate_and_heal(out_mp4, prompt, _who, _emph, _i2v_regen,
                       progress_cb, dry_run, manifests, tag,
                       scene_ref_path=scene_ref_path,
                       expected_facts=_set_expected_facts(_i2v_sa),
                       strict_blaze=_is_last_region)

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
        next_cc = concept_cuts[i + 1] if i + 1 < len(concept_cuts) else {}
        is_last_overall = (i == len(cuts) - 1)
        # PD 2026-06-09: a CHAINED one-take must FLOW — each cut chains from the prev
        # cut's last frame, so fading to black between every cut (old logic) created
        # visible ~0.07s black flashes at each boundary (Giri "검은 프레임 삽입"). Fade
        # ONLY at the episode open (cut1) and at TRUE scene boundaries (a cut that is
        # NOT chained from the previous). Between chained cuts → HARD CUT (seamless).
        # The 여운 (last cut) is NOT a fade — it's +2s longer (Step 3b) with the caption
        # held, so the final cut never fades to black.
        chained = bool(cc.get("chain_from_prev"))
        next_chained = bool(next_cc.get("chain_from_prev"))
        # PD 2026-06-11: NO black fade BETWEEN cuts — PD asked for the inter-cut
        # fade-to-black to be removed (it caused a visible black dip at every non-
        # chained scene boundary: this cut faded OUT to black + the next faded IN
        # from black). Only the very first cut fades in from black (gentle episode
        # open after the intro bumper). Every other boundary is a HARD CUT, and when
        # CHAIN_TRANSITION=crossfade the assembler dissolves ALL boundaries (below)
        # so scene changes are smooth without any black.
        fade_in_d = 0.4 if i == 0 else 0.0
        fade_out_d = 0.0
        # Build filter expression
        filters = []
        if fade_in_d > 0:
            filters.append(f"fade=t=in:st=0:d={fade_in_d}")
        if fade_out_d > 0:
            fade_out_st = max(0, dur - fade_out_d)
            filters.append(f"fade=t=out:st={fade_out_st}:d={fade_out_d}")
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
        label = "open" if i == 0 else ("chained(hard-cut)" if chained else "scene-cut")
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
    av_linger = float(os.getenv("AV_END_LINGER_S", "2.0"))
    for i, item in enumerate(cuts):
        tag = item.get("tag")
        cc = concept_cuts[i] if i < len(concept_cuts) else {}
        tgt = int(cc.get("target_duration_seconds") or 0)
        src = int(cc.get("duration_seconds") or 0)
        is_last = (i == len(cuts) - 1)
        src_mp4 = anim_dir / f"{tag}.mp4"
        if not src_mp4.exists():
            continue
        if is_last and av_linger > 0.1:
            # 여운 (PD 2026-06-08 CORRECTED): the last cut plays ~2s LONGER with the
            # motion still going (NOT a frozen frame) and the caption held over it
            # (_hold_final_caption). av has no extra generated footage, so gently slow
            # the cut to add the linger — continuous slowed motion, not a freeze.
            try:
                actual = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(src_mp4)],
                    capture_output=True, text=True, timeout=15).stdout.strip() or (src or 5))
            except Exception:
                actual = float(src or 5)
            if actual <= 0.1:
                continue
            ratio = (actual + av_linger) / actual
            slowed_mp4 = anim_dir / f"{tag}_slow.mp4"
            cmd = [
                "ffmpeg", "-y", "-i", str(src_mp4),
                "-filter:v", f"setpts={ratio:.3f}*PTS",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "fast", "-crf", "18", str(slowed_mp4),
            ]
            _run(cmd, f":hourglass: [3b/6] 여운 {tag} {actual:.1f}s→{actual+av_linger:.1f}s "
                 f"(last cut +{av_linger:.0f}s, motion continues)", progress_cb, dry_run)
            if not dry_run and slowed_mp4.exists():
                src_mp4.unlink()
                slowed_mp4.rename(src_mp4)
            continue
        # PD 2026-06-10: control EPISODE LENGTH per video. The fast Seedance model is
        # locked to 5s/cut (it rejects 3/4s), so the only way to make a multi-cut Short
        # the right length is to retime each cut in post. target can now be SHORTER than
        # src (speed-up via setpts<1) — unlike a tail-trim this keeps the chain seamless
        # (no jump at the boundary). AV_CUT_OUTPUT_SECONDS sets the default target for AV
        # cuts when the Director didn't (e.g. 4 → 5s cut plays in 4s, subtle 1.25x).
        if tgt <= 0:
            tgt = int(os.getenv("AV_CUT_OUTPUT_SECONDS", "0") or 0)
        if tgt <= 0 or src <= 0 or tgt == src or not tag:
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
        _verb = "Slow" if ratio > 1 else "Speed"
        _run(cmd, f":hourglass: [3b/6] {_verb} {tag} {src}s→{tgt}s ({ratio:.2f}x)",
             progress_cb, dry_run)
        if not dry_run and slowed_mp4.exists():
            src_mp4.unlink()
            slowed_mp4.rename(src_mp4)

    # Step 3c (PD 2026-07-13): lo-fi grade for real-look AV cuts. A "real filmed" AV episode
    # (challenge / daily) came back too clean/glossy because Seedance ref-mode ignores the LO-FI
    # *prompt* — so grade the finished cut deterministically. Real-look = ai_vtuber AND not a
    # fantasy beat (fantasy keeps its vivid dreamscape). Applied here (before persist + burn) so
    # the persisted cuts and any re-caption reuse the graded footage, captions stay crisp on top.
    if (card.get("render_style") or "").lower() == "ai_vtuber":
        for i, item in enumerate(cuts):
            cc = concept_cuts[i] if i < len(concept_cuts) else {}
            if _cut_is_fantasy(cc):
                continue
            _apply_av_lofi_grade(anim_dir / f"{item['tag']}.mp4", progress_cb, dry_run)

    # Step 4: build bumpers if needed
    if not INTRO_BUMPER.exists() or not OUTRO_BUMPER.exists():
        _run(
            [sys.executable, "scripts/build_bumpers.py",
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

    # Step 4c (#3): Editor agent for AV. Runs AFTER the VLM rewrite (so it judges the
    # ACTUAL rendered footage via vlm_actual_action). CONSERVATIVE: tempo + intent↔
    # footage mismatch only — reorder/drop are disabled for a chained AV one-take
    # (would break continuity); allowed only when the episode is NOT chained.
    if not dry_run and os.getenv("EDITOR_AGENT", "1") != "0":
        try:
            _chained = bool(manifests.get("chain_mode") or card.get("chain_mode"))
            _struct = (not _chained) and os.getenv("EDITOR_AV_STRUCTURAL", "0") == "1"
            _plan = _run_editor(manifests.get("concept") or card or manifests,
                                manifests, "ai_vtuber", progress_cb)
            if _plan:
                _apply_edit_plan(manifests, _plan, anim_dir, progress_cb,
                                 allow_structural=_struct)
                manifests["_edit_plan"] = _plan
        except Exception as ex:
            log.warning("AV editor pass failed (keeping render): %s", ex)

    # Step 4d-anchor: same memory-lane time-spine guarantee as RF (retro C13) — the VLM
    # rewrite grounds on what's on screen and can drop the Writer's 과거⇄현재 anchor on an
    # AV memory-lane; restore opener/closer before burn. No-op unless multi-year.
    _enforce_memorylane_anchors(manifests, anim_dir, progress_cb, dry_run)

    # Step 5: burn captions (손글씨 기본, Director font_override 가능)
    # PD 2026-06-17: per-EPISODE captioned dir (not the shared junk-drawer).
    captioned_dir = work_dir / "animated_captioned"
    _run(
        _burn_captions_cmd(manifests, anim_dir, captioned_dir),
        ":speech_balloon: [5/6] Burning captions",
        progress_cb, dry_run,
    )

    # Step 6: assemble
    out = ROOT / "data" / "output" / "episodes" / f"episode_av_{ts}.mp4"
    asm_cmd = [
        sys.executable, "scripts/assemble_episode.py",
        "--captions", manifests["captions"],
        "--in-dir", str(captioned_dir),
        "--intro-bumper", str(INTRO_BUMPER),
        "--outro-bumper", str(OUTRO_BUMPER),
        "--music", manifests.get("bgm", str(DEFAULT_BGM)),
        "--out", str(out),
    ]
    # PD 2026-06-09/11 보강 옵션: CHAIN_TRANSITION=crossfade → cuts dissolve instead
    # of any black fade. PD 2026-06-11: apply the dissolve to EVERY cut boundary, not
    # just chain_from_prev ones — the inter-cut fade-to-black is now removed entirely
    # (above), so a non-chained SCENE boundary would otherwise hard-cut; a short 0.2s
    # dissolve makes scene changes smooth with no black dip (PD: "컷 사이 검정 f/o 빼기").
    if os.getenv("CHAIN_TRANSITION", "hardcut").lower() == "crossfade":
        xfade_tags = [it.get("tag") for j, it in enumerate(cuts)
                      if j > 0 and it.get("tag")]
        if xfade_tags:
            asm_cmd += ["--xfade-tags", ",".join(xfade_tags),
                        "--xfade-dur", os.getenv("CHAIN_XFADE_DUR", "0.2")]
    # PD 2026-06-11: persist render params so a Giri caption-fail can be salvaged
    # (re-caption these rendered cuts, no Seedance re-render).
    _persist_render_meta(work_dir, manifests, cuts, concept_cuts,
                         xfade_tags=locals().get("xfade_tags"),
                         style="ai_vtuber")
    _run(
        asm_cmd,
        ":clapper: [6/6] Final assembly",
        progress_cb, dry_run,
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Main entry: render_card
# ──────────────────────────────────────────────────────────────────────
def _protected_workdirs(dirs: list[Path]) -> set[Path]:
    """PD 2026-07-09: a workdir's animated/<tag>.mp4 cuts are the ONLY $0 source for a
    caption-salvage (re-caption without paying Seedance again). So a workdir must NOT be
    pruned while its episode is still IN-FLIGHT — scheduled but not yet public, or published
    so recently that PD might still send a fix. Blind keep-newest-N deleted a scheduled 7/9
    AV's salvage source and forced a paid re-render; this makes prune episode-state-aware.

    Protected = its card (by render_meta.card_id) is a recent/future batch AND either not yet
    uploaded, or published within the salvage window (CAMERAMAN_SALVAGE_DAYS, default 3).
    Stale un-published junk (old drafts/vetoes) is NOT protected, so disk stays bounded."""
    salvage_days = int(os.getenv("CAMERAMAN_SALVAGE_DAYS", "3"))
    protected: set[Path] = set()
    try:
        con = _db()
    except Exception as e:
        log.warning("prune protect: db open failed (%s) — protecting nothing", e)
        return protected
    try:
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()
        for d in dirs:
            try:
                cid = json.loads((d / "render_meta.json").read_text(
                    encoding="utf-8")).get("card_id")
            except Exception:
                continue  # no meta → keep-newest-N still covers the recent ones
            if not cid:
                continue
            row = con.execute(
                "SELECT date, state, uploaded, youtube_publish_at FROM cards WHERE card_id=?",
                (cid,)).fetchone()
            if not row:
                continue
            cdate, state, uploaded, pub = row[0], row[1], row[2] or 0, row[3]
            # A future publish time is always in-flight (protect regardless of card date).
            if pub:
                try:
                    p = dt.datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
                    if p > now - dt.timedelta(days=salvage_days):
                        protected.add(d)
                        continue
                except Exception:
                    protected.add(d)  # unparseable schedule → be safe
                    continue
            # Not-yet-public AND part of a current/future batch (recent date) → in-flight.
            try:
                recent = cdate and cdate >= (today - dt.timedelta(days=salvage_days)).isoformat()
            except Exception:
                recent = False
            if uploaded == 0 and state != "archived" and recent:
                protected.add(d)
    except Exception as e:
        log.warning("prune protect scan failed: %s", e)
    finally:
        con.close()
    return protected


def _prune_tmp_workdirs(keep: int | None = None) -> None:
    """PD 2026-06-06: delete old cameraman_* tmp workdirs (trimmed clips,
    photo_i2v, animated intermediates) — they accumulated to 16GB. Keep the
    most recent `keep` for debugging. Final episodes live in data/output/
    episodes and are never touched. Override count with CAMERAMAN_TMP_KEEP.

    PD 2026-07-09: ALSO keep any workdir whose episode is still in-flight (see
    `_protected_workdirs`) — never delete the salvage source of a scheduled or
    just-published episode just because 6 newer renders happened.

    PD 2026-07-12: retain by AGE, not just count — keep EVERY workdir from the last
    CAMERAMAN_TMP_KEEP_DAYS (default 7) days. The count-only rule pruned a still-scheduled
    episode's source, which forced a full $50 AV re-render for a one-word caption fix.
    A 7-day window means a caption fix / salvage almost always has its source. (Lower the
    env knob if disk pressures; the in-flight protection + newest-N floor still apply.)"""
    if keep is None:
        keep = int(os.getenv("CAMERAMAN_TMP_KEEP", "6"))
    keep_days = float(os.getenv("CAMERAMAN_TMP_KEEP_DAYS", "7"))
    try:
        import time as _time
        cutoff = _time.time() - keep_days * 86400.0
        tmp = ROOT / "data" / "tmp"
        dirs = sorted(
            [d for d in tmp.glob("cameraman_*") if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        protected = _protected_workdirs(dirs)
        removed = 0
        for i, d in enumerate(dirs):
            # keep: newest-N floor, in-flight/scheduled, OR within the 7-day window
            if i < keep or d in protected or d.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        if removed:
            log.info("pruned %d old tmp workdirs (kept newest %d + %d in-flight + <%.0fd)",
                     removed, keep, len(protected), keep_days)
    except Exception as e:
        log.warning("tmp prune failed: %s", e)


def _persist_render_meta(work_dir: Path, manifests: dict, cuts: list,
                         concept_cuts: list | None = None,
                         xfade_tags: list | None = None,
                         style: str = "") -> None:
    """PD 2026-06-11 캡션-salvage: persist the few render parameters that are NOT
    re-derivable from disk (BGM track, cut order, xfade tags, the concept) so a
    Giri caption-failure can be SALVAGED — re-caption the already-rendered cuts in
    work_dir/animated and re-assemble with the SAME BGM — instead of throwing the
    expensive Seedance render away and re-proposing. captions.json + animated/
    already persist; this fills the gap. Best-effort, never raises."""
    try:
        meta = {
            "card_id": (manifests.get("card_id")
                        or (manifests.get("concept") or {}).get("card_id") or ""),
            "style": style or manifests.get("render_style") or "",
            "bgm": manifests.get("bgm") or str(DEFAULT_BGM),
            "captions": str(manifests.get("captions") or (work_dir / "captions.json")),
            "anim_dir": str(work_dir / "animated"),
            "cut_tags": [c.get("tag") for c in (cuts or []) if c.get("tag")],
            "xfade_tags": list(xfade_tags or []),
            "font_override": manifests.get("font_override") or "",
            "concept": manifests.get("concept") or {},
            "concept_cuts": concept_cuts or [],
            "_edit_plan": manifests.get("_edit_plan"),  # #3: Editor intent↔footage verdict
        }
        (work_dir / "render_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("render_meta persist failed (salvage may be unavailable): %s", e)


def _log_batch_problem(rec: dict) -> None:
    """Append a structured batch-failure record so launch problems are DIAGNOSABLE
    after the fact. PD 2026-06-21: prefetch failures used to be swallowed — only a
    generic '드롭' surfaced, so when the 03:00 batch lost slots we couldn't tell why
    (slow osxphotos window vs genuinely-missing asset). This writes the real cause to
    data/logs/batch_problems.jsonl AND to stderr (→ launch.err.log) so both the PD's
    Slack and the on-disk logs carry it."""
    try:
        import datetime as _dt
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), **rec}
        p = ROOT / "data" / "logs" / "batch_problems.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.warning("BATCH PROBLEM: %s", json.dumps(rec, ensure_ascii=False))
    except Exception as e:
        log.warning("batch problem log failed: %s", e)


def _prestage_concept_assets(concept: dict | None, card: dict | None,
                             progress_cb: ProgressCb = None) -> None:
    """PD 2026-06-11: DOWNLOAD all of a concept's source assets to disk BEFORE the
    render starts (serialized via the osxphotos lock), so the render never dies
    mid-preprocess on a missing photo ("not found"). The efficient-storage model
    keeps originals only on-demand; pre-staging front-loads every re-download in one
    serial pass and surfaces a genuinely-unavailable asset before any render time is
    spent. Missing-after-prestage assets are left for the per-cut gate to swap/drop."""
    cuts = list((concept or {}).get("cuts") or [])
    if not cuts and card:
        try:
            cuts = json.loads(card.get("payload_json") or "{}").get("cuts") or []
        except Exception:
            cuts = []
    aids: list[str] = []
    for c in cuts:
        for k in ("asset_id", "secondary_asset_id"):
            if c.get(k):
                aids.append(c[k])
    aids = list(dict.fromkeys(aids))
    if not aids:
        return
    con = _db()
    need = []
    for aid in aids:
        try:
            r = con.execute("SELECT file_path, source_uuid FROM assets WHERE asset_id=?",
                            (aid,)).fetchone()
        except Exception:
            r = None
        if not r:
            continue
        fp, uuid = r[0], r[1]
        if fp and not Path(fp).is_absolute():
            fp = str(ROOT / fp)
        if fp and uuid and not Path(fp).exists():
            need.append((aid, fp, uuid))
    if not need:
        return
    # GCS-first bulk prefetch (PD 2026-06-21): pull whatever is mirrored to GCS —
    # fast/reliable, no Photos-library open / PhotoKit / dawn window. Only GCS-misses
    # (assets not yet mirrored) fall through to the osxphotos export below. This is the
    # durable fix for the recurring dawn download failures.
    try:
        from icloud import gcs as _gcs
        if _gcs.enabled():
            still, g_ok = [], 0
            for aid, fp, uuid in need:
                if _gcs.download_to(fp):
                    g_ok += 1
                else:
                    still.append((aid, fp, uuid))
            if g_ok and progress_cb:
                progress_cb(f":cloud: GCS에서 {g_ok}개 선다운로드 (osxphotos 불필요)")
            need = still
        if not need:
            return
    except Exception as _e:
        log.warning("gcs bulk prefetch failed, falling back to osxphotos: %s", _e)
    # Cloud VM / non-Mac: osxphotos can't run here (no Photos library). Whatever GCS
    # didn't have stays in `need`; the per-cut gate swaps/drops it. The mirror is ~100%
    # complete, so a miss is rare — this keeps the render path GCS-only off the Mac.
    from icloud.sync import _osxphotos_available
    if not _osxphotos_available():
        if need and progress_cb:
            progress_cb(f":cloud: GCS-only 모드 — osxphotos 미가용, 미러 누락 {len(need)}개는 컷 게이트가 처리")
        return
    # PD 2026-06-16: download iCloud-only originals UPFRONT in ONE bulk osxphotos
    # export (a single --uuid-from-file call = one library scan for the whole set),
    # then place each {uuid}.ext at its expected file_path. The old per-photo loop
    # made osxphotos re-scan the 400k-item Photos DB once PER photo (~20s each); 7
    # photos = 7 scans, and under launch contention every scan+download blew the
    # 90s/photo budget → AV got 0/7 → 0 cuts → slot skipped (the 6/17 AV-전멸).
    # One scan + bulk export does the same 7 photos in seconds. Whatever still
    # doesn't arrive is left for the per-cut gate to swap/drop; render never downloads.
    # PD 2026-06-21: bumped 600→1200 so the prefetch can WAIT OUT a transient slow
    # osxphotos window (common in the 03:00 batch hour). With the per-attempt cap +
    # slow-window backoff in download_assets_by_uuids, the extra budget = more retries
    # to catch the window recovering — the 03:00 batch has hours before review/publish.
    budget = float(os.getenv("PREFETCH_BUDGET", "1200"))
    if progress_cb:
        progress_cb(f":arrow_down: 렌더 전 자산 사전 다운로드 {len(need)}개 (일괄 1회, 예산 {int(budget)}s)")
    from icloud.sync import download_assets_by_uuids
    staging = ROOT / "data" / "tmp" / "prefetch_staging"
    got = download_assets_by_uuids([uuid for _aid, _fp, uuid in need], staging,
                                   timeout=budget)
    ok = 0
    failed: list[str] = []
    for aid, fp, uuid in need:
        src = got.get(uuid)
        if src and Path(src).exists():
            try:
                Path(fp).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, fp)
                ok += 1
                continue
            except Exception:
                if Path(src).exists():  # move failed but bytes exist — still usable
                    ok += 1
                    continue
        failed.append(str(aid))
    if failed:
        # Diagnose WHY instead of swallowing it: a quick health probe distinguishes a
        # transient slow-osxphotos window (common in the 03:00–06:00 system-maintenance
        # hours — recovers on its own) from a genuinely-unavailable asset (iCloud not
        # synced / file gone / permissions). Record it so the cause is never lost again.
        try:
            from icloud.sync import _osxphotos_healthy
            healthy = _osxphotos_healthy(probe_timeout=45)
        except Exception:
            healthy = None
        reason = ("osxphotos SLOW WINDOW — 라이브러리 열기가 느려 다운로드가 예산 내 미완료 "
                  "(일시적; 새벽 시스템 유지보수 시간대에 잦음, 보통 스스로 회복)"
                  if healthy is False else
                  "osxphotos 정상 — 다운로드 자체 실패(iCloud 미동기화/파일 손상/권한 가능)"
                  if healthy else "osxphotos 상태 확인 불가")
        _title = (concept or {}).get("title") or (card or {}).get("theme") or "?"
        if isinstance(_title, dict):
            _title = _title.get("ko") or _title.get("en") or "?"
        _log_batch_problem({
            "stage": "prefetch", "concept": str(_title)[:80],
            "card_id": (card or {}).get("card_id"),
            "budget_s": int(budget), "ok": ok, "total": len(need),
            "failed_assets": failed, "osxphotos_healthy": healthy, "reason": reason,
        })
        if progress_cb:
            for aid in failed:
                progress_cb(f":warning: 사전 다운로드 실패 {aid[:24]} — 렌더 중 교체/드롭")
            progress_cb(f":rotating_light: 사전 다운로드 {ok}/{len(need)} — 원인: {reason} "
                        f"(상세: data/logs/batch_problems.jsonl)")
    if progress_cb:
        progress_cb(f":white_check_mark: 사전 다운로드 {ok}/{len(need)} 완료")


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

        # PD 2026-06-11: pre-download ALL source assets up front (serialized) so the
        # render never fails mid-preprocess on a pruned/missing photo ("not found").
        if not dry_run:
            try:
                _prestage_concept_assets(concept, card, progress_cb)
            except Exception as e:
                log.warning("asset prestage failed (non-fatal): %s", e)

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
