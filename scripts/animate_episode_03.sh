#!/bin/bash
# scripts/animate_episode_03.sh
# Episode 03 Ryani판 — 부처님 오신날 / 수묵화 톤.
# 4 cuts from regen'd ink-wash style PNGs → 4 short mp4 clips.
#
# Usage:
#   bash scripts/animate_episode_03.sh                       # all 4
#   bash scripts/animate_episode_03.sh cut3_ryani_with_leo   # single (re-run/debug)
#
# Env:
#   VEO_MODEL    override (default veo-3.1-lite-generate-preview)
#   FORCE=1      re-run even if mp4 exists
#
# Cost: 4 cuts × ~$0.60 (Veo 3.1 lite) = ~$2.40
#
# Motion philosophy (수묵화 톤):
#   - Gentle, meditative motion. No dance party energy here.
#   - Lanterns drift slowly. Ink mist rises. Pets breathe / blink.
#   - Lite tier is sufficient — Vertex Veo 3.0 standard would be overkill
#     and might introduce too-aggressive motion that breaks the serene mood.
#
# Same prompt rules from notes/sora2_motion_lessons.md §6:
#   - VERIFIED dual-motion pattern for multi-subject (cut3 only).
#   - Cat tail MUST swish when cat in frame.
#   - Avoid proper nouns (Leo / Ryani).
#   - Mention "no tail" for the French bulldog.
#   - One modifier per action ("slowly", "gently"), no emphasis-stacking.

set -euo pipefail
cd "$(dirname "$0")/.."

REGEN_DIR="data/tmp/episode_03_regen"
OUT_DIR="data/output/animated"
mkdir -p "$OUT_DIR"

VEO_MODEL="${VEO_MODEL:-veo-3.1-lite-generate-preview}"
SECS=4
FORCE="${FORCE:-0}"
TARGET_TAG="${1:-}"

animate_one() {
  local tag="$1" prompt="$2"
  if [ -n "$TARGET_TAG" ] && [ "$TARGET_TAG" != "$tag" ]; then
    return 0
  fi
  local img="${REGEN_DIR}/${tag}.png"
  local out="${OUT_DIR}/${tag}.mp4"
  if [ ! -f "$img" ]; then
    echo "  ! ${tag}: MISSING $img — run regen_vtuber_style.py with --prompts episode_03_regen_prompts.json first"
    return 0
  fi
  if [ -f "$out" ] && [ "$FORCE" != "1" ]; then
    echo "==> ${tag}  (exists, skipping — set FORCE=1 to re-run)"
    return 0
  fi
  echo "==> ${tag}  ($VEO_MODEL)"
  python3 scripts/animate_hero_veo3.py \
    --image "$img" \
    --prompt "$prompt" \
    --seconds "$SECS" \
    --model "$VEO_MODEL" \
    --output "$out"
  echo "  ok → $out"
  echo
}

echo "==> Episode 03 Ryani판 i2v (4 cuts via $VEO_MODEL, ${SECS}s each)"
echo "    input  : $REGEN_DIR"
echo "    output : $OUT_DIR"
echo

# ─── cut1: Ryani greeting (single subject — close-up portrait) ─────────
animate_one cut1_ryani_greeting \
  "A small black-and-white French bulldog without a tail sits at a cafe, looking directly at the camera. The dog slowly blinks and its ears twitch gently. A paper lantern in the corner gently sways with warm golden glow. Ink-wash mist drifts softly. Camera holds still."

# ─── cut2: Ryani contemplative (single subject — meditation) ───────────
animate_one cut2_ryani_contemplate \
  "A small black-and-white French bulldog without a tail sits in profile, looking up serenely. The dog breathes slowly and gently flicks an ear. Bamboo silhouettes sway softly in the breeze. A paper lantern's warm gold light pulses gently. Ink mist rises. Camera holds still."

# ─── cut3: Ryani with Leo (multi-subject — dual motion) ────────────────
animate_one cut3_ryani_with_leo \
  "A small black-and-white French bulldog without a tail and an orange tabby cat stand close together at a Korean temple courtyard. The dog tilts its head slightly toward the camera and blinks. At the same time the cat slowly swishes its tail and nuzzles the dog's cheek. Paper lanterns drift gently overhead. Soft gold dust falls. Camera holds still."

# ─── cut4: Ryani peaceful close (single subject — wrap) ────────────────
animate_one cut4_ryani_peaceful \
  "A small black-and-white French bulldog without a tail rests in a vintage chair. The dog breathes slowly — chest rises and falls. A whisker twitches gently. In the background sky, paper lanterns drift upward in warm gold and deep red. Ink-wash mist softly rises. Camera holds still."

echo
echo "Done. Episode 03 clips:"
ls -lh "$OUT_DIR"/cut1_ryani_greeting.mp4 \
       "$OUT_DIR"/cut2_ryani_contemplate.mp4 \
       "$OUT_DIR"/cut3_ryani_with_leo.mp4 \
       "$OUT_DIR"/cut4_ryani_peaceful.mp4 2>/dev/null || true
