"""scripts/gen_bgm_library.py — seed a Suno-generated BGM library, tagged by the channel's
concept MOODS, so the daily RF/AV pipeline can use concept-matched original music instead of the
static licensed library (PD 2026-11-11: "배경 음악은 컨셉 기준으로 suno로 만들어").

DESIGN (library-match, PD-approved): generating a fresh Suno track per episode is slow (1-3 min)
and paid — too costly for a daily batch. So we seed a POOL of instrumental tracks per mood OFFLINE
here, and agents.cameraman._pick_bgm_track selects from that pool at render time (no generation on
the hot path). Each track is archived to assets/bgm/generated/ with a `mood` tag in manifest.jsonl
(git-tracked → deploys to the VM). Run this once to seed, then occasionally to grow/refresh a mood.

The mood vocabulary MIRRORS agents.cameraman._BGM_MOOD_MAP (the concept's bgm_mood), so a seeded
mood slots straight into selection. Prompts are concept-aware for THIS channel: a tailless black
French bulldog big-sister (랴니) + an orange tabby cat (레오), wholesome pet-vlog, always instrumental.

  python scripts/gen_bgm_library.py --per-mood 2                 # seed every mood to 2 tracks
  python scripts/gen_bgm_library.py --moods cozy,warm_acoustic   # only these moods
  python scripts/gen_bgm_library.py --per-mood 3 --dry-run       # show the gap + prompts, no cost

Cost: ~Suno gen per track (only fills the GAP to --per-mood; already-seeded moods are skipped).
SUNO_API_KEY required. Kill-switch for CONSUMPTION is USE_GENERATED_BGM (read by cameraman); this
tool only produces data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.gen_song import generate_song, MANIFEST  # noqa: E402

# Concept-aware instrumental prompts per mood. Keys MUST match agents.cameraman._BGM_MOOD_MAP.
# Every prompt is INSTRUMENTAL (no vocals) and wholesome-pet-channel; the mood sets the energy.
_COMMON = (" wholesome Korean pet-vlog background instrumental for a cute cat-and-dog channel, "
           "warm and family-friendly, loopable, NO vocals, no sudden stingers.")
MOOD_PROMPTS: dict[str, str] = {
    "gentle_acoustic": "gentle fingerpicked acoustic guitar with soft piano, tender and unhurried,"
                       " a calm sunny afternoon feel," + _COMMON,
    "warm_acoustic": "warm mid-tempo acoustic guitar with light piano and soft strings, cozy and "
                     "heartwarming, a little nostalgic," + _COMMON,
    "soft_lofi": "soft lo-fi hip-hop, mellow Rhodes keys, dusty vinyl crackle, lazy boom-bap "
                 "brush drums, cozy study-beats calm," + _COMMON,
    "gentle_ambient": "gentle ambient pad with soft flute and light bell tones, airy and peaceful,"
                      " slow and spacious, meditative but warm," + _COMMON,
    "cozy": "cozy warm acoustic guitar and felt piano, soft and intimate, a snuggled-on-the-sofa "
            "winter-afternoon feel," + _COMMON,
    "lullaby": "soft music-box and gentle celesta lullaby with light harp, tender and sleepy, very "
               "slow and soothing," + _COMMON,
    "playful_upbeat": "playful upbeat acoustic pop, bouncy pizzicato strings and claps, cheerful "
                      "mid tempo, bright and smile-inducing," + _COMMON,
    "playful_synth": "playful bouncy synth-pop, round plucky synths and marimba, quirky and light,"
                     " a cute giggly bounce," + _COMMON,
    "upbeat_cute": "cute cheerful childrens-style instrumental, glockenspiel and whistling melody, "
                   "light claps, bright and adorable," + _COMMON,
    "fun": "fun lively instrumental, jaunty piano and brushed drums with handclaps, happy-go-lucky "
           "energy, feel-good bounce," + _COMMON,
    "whistle": "bright carefree whistling melody over sunny acoustic guitar and light shaker, "
               "happy strolling tempo, feel-good," + _COMMON,
    "ukulele": "sunny upbeat ukulele with light whistling and handclaps, carefree island-breezy "
               "happiness, easy mid tempo," + _COMMON,
    "chill_documentary": "chill documentary bed, warm electric piano and soft bossa-nova brush "
                         "drums, relaxed observational calm," + _COMMON,
    "chill": "relaxed chill instrumental, mellow guitar and soft pad with light shaker, laid-back "
             "and easygoing, unhurried," + _COMMON,
    "happy": "happy bright instrumental, cheerful piano and strings with claps, sunny optimistic "
             "energy, warm major-key joy," + _COMMON,
    "jazz_quirky": "quirky light jazz, walking upright bass, muted trumpet and brushed snare, "
                   "playful cat-and-mouse swing, witty and bouncy," + _COMMON,
    "comedy": "quirky comedic instrumental, pizzicato strings and bassoon with a mischievous "
              "tiptoe feel, light and funny, cartoonish but tasteful," + _COMMON,
}


def _seeded_counts() -> dict:
    counts: dict[str, int] = {}
    try:
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            m = rec.get("mood")
            if m and (ROOT / "assets" / "bgm" / "generated" / (rec.get("file") or "")).exists():
                counts[m] = counts.get(m, 0) + 1
    except Exception:
        pass
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-mood", type=int, default=2, help="target tracks per mood (fills the gap)")
    ap.add_argument("--moods", help="comma-separated subset of moods (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="show gap + prompts, no generation/cost")
    a = ap.parse_args()

    moods = [m.strip() for m in a.moods.split(",")] if a.moods else list(MOOD_PROMPTS)
    bad = [m for m in moods if m not in MOOD_PROMPTS]
    if bad:
        print(f"unknown moods (not in MOOD_PROMPTS / _BGM_MOOD_MAP): {bad}")
        return 2
    have = _seeded_counts()
    out_dir = ROOT / "data" / "tmp" / "bgm_library"
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = [(m, max(0, a.per_mood - have.get(m, 0))) for m in moods]
    total = sum(n for _, n in plan)
    print(f"seeded now: { {m: have.get(m, 0) for m in moods} }")
    print(f"to generate (gap to {a.per_mood}/mood): {total} tracks")
    if a.dry_run:
        for m, n in plan:
            if n:
                print(f"  {m}: +{n}\n     {MOOD_PROMPTS[m]}")
        return 0

    made = failed = 0
    for m, n in plan:
        for i in range(n):
            try:
                generate_song(out_mp3=out_dir / f"{m}_{i}.mp3", instrumental=True,
                              prompt=MOOD_PROMPTS[m], title=f"랴니레오 BGM {m}", mood=m)
                made += 1
            except Exception as e:
                failed += 1
                print(f"  ! {m} #{i}: {e}", flush=True)
    print(f"DONE seeded={made} failed={failed}. Flip consumption with USE_GENERATED_BGM=1 after a "
          f"listen (archived + manifest-tagged under assets/bgm/generated/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
