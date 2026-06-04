#!/bin/bash
# scripts/animate_episode_02.sh
# Episode 02 — 부처님 오신날.
# 4 cuts from regen'd vtuber-style PNGs (data/tmp/episode_02_regen/) →
# 4 short mp4 clips (data/output/animated/cut{1..4}_*.mp4, 4s each).
#
# Usage:
#   bash scripts/animate_episode_02.sh                   # all 4 cuts
#   bash scripts/animate_episode_02.sh cut3_dance_party  # one cut (re-run / debug)
#
# Env vars:
#   VEO_MODEL   override (default veo-3.1-lite-generate-preview, A/B/C winner)
#   FORCE=1     re-run even if output mp4 already exists
#
# Cost: 4 cuts × ~$0.60 = ~$2.40 (Veo 3.1 lite)
#
# Motion prompt rules (carried over from notes/sora2_motion_lessons.md §6-7):
#   - VERIFIED dual-motion pattern for multi-subject cuts:
#       "An A and a B ... The A slowly Xs. At the same time the B Ys.
#        Camera gently pushes in / holds still."
#   - Cat tail MUST swish when cat in frame (mandatory primary motion).
#   - Avoid proper nouns (Leo / Ryani) — Veo+Sora moderation blocks them
#     intermittently. Refer to subjects by species/color/breed.
#   - Avoid warp/animate/morph verbs (also moderation-prone).
#   - Mention "no tail" for the French bulldog so the model doesn't
#     hallucinate a tail (Ryani is brachycephalic Frenchie).
#   - One modifier ("slowly", "gently") per action — don't stack emphases.
#   - For single-subject cuts (cut1, cut2): describe one animal + ambient
#     environment motion (drifting petals, sunbeam shimmer).
#   - For multi-subject cuts (cut3, cut4): use the dual-motion pattern.
#     Episode 01 lesson: smaller/darker subject may go static — acceptable
#     at Shorts pacing.

set -euo pipefail
cd "$(dirname "$0")/.."

REGEN_DIR="data/tmp/episode_02_regen"
OUT_DIR="data/output/animated"
mkdir -p "$OUT_DIR"

VEO_MODEL="${VEO_MODEL:-veo-3.1-lite-generate-preview}"
SECS=4
FORCE="${FORCE:-0}"

# optional arg: single cut tag
TARGET_TAG="${1:-}"

# Cuts that need richer motion than Veo 3.1 lite provides. These are routed
# to Vertex AI Veo 3.0 standard (cost ~$4 vs lite $0.60, but much higher
# baseline motion quality). 2026-05-20: lastFrame interpolation tried and
# rejected by every model variant we have access to ("request not supported
# by this model" or 404). Falling back to: Vertex Veo 3.0 standard + ffmpeg
# overlay animation post-hoc for extra frame energy.
VERTEX_CUTS=("cut3_dance_party")

is_vertex_cut() {
  local needle="$1"
  for c in "${VERTEX_CUTS[@]}"; do
    [ "$c" = "$needle" ] && return 0
  done
  return 1
}

animate_one() {
  local tag="$1" prompt="$2"
  if [ -n "$TARGET_TAG" ] && [ "$TARGET_TAG" != "$tag" ]; then
    return 0
  fi
  local img="${REGEN_DIR}/${tag}.png"
  local out="${OUT_DIR}/${tag}.mp4"
  if [ ! -f "$img" ]; then
    echo "  ! ${tag}: MISSING $img — run regen_vtuber_style.py first"
    return 0
  fi
  if [ -f "$out" ] && [ "$FORCE" != "1" ]; then
    echo "==> ${tag}  (exists, skipping — set FORCE=1 to re-run)"
    return 0
  fi
  if is_vertex_cut "$tag"; then
    local model_used="${VERTEX_MODEL:-veo-3.0-generate-001}"
    echo "==> ${tag}  (Vertex $model_used — richer motion)"
    python3 scripts/animate_hero_veo3_vertex.py \
      --image "$img" \
      --prompt "$prompt" \
      --seconds "$SECS" \
      --model "$model_used" \
      --output "$out"
  else
    echo "==> ${tag}  ($VEO_MODEL)"
    python3 scripts/animate_hero_veo3.py \
      --image "$img" \
      --prompt "$prompt" \
      --seconds "$SECS" \
      --model "$VEO_MODEL" \
      --output "$out"
  fi
  echo "  ok → $out"
  echo
}

echo "==> Episode 02 i2v (4 cuts via $VEO_MODEL, ${SECS}s each)"
echo "    input dir : $REGEN_DIR"
echo "    output dir: $OUT_DIR"
echo

# ─── cut1: peony greeting (single subject) ─────────────────────────────
animate_one cut1_peony_greeting \
  "An orange tabby cat sits beside a vase of pink peony flowers. The cat slowly swishes its tail and flicks an ear. Pink lotus petals drift down gently in the background. Camera gently pushes in toward the cat."

# ─── cut2: sunbathe / meditation (single subject) ──────────────────────
animate_one cut2_sunbathe_meditate \
  "An orange tabby cat lies stretched out on the ground, paws reaching toward bamboo leaves. The cat slowly flexes a paw and blinks softly. Golden sunbeams shimmer gently. Cherry blossom petals drift down. Camera holds still."

# ─── cut3: dance party (multi-subject — high energy) ───────────────────
# Veo 3.1 lite default motion is gentle. For party energy we lean on:
#   1) stronger action verbs ("bobs", "rocks", "sways", "shakes") instead of
#      "slowly tilts" — keeps Veo's motion budget pointing at the subjects.
#   2) explicit environmental motion (confetti SWIRLS, lights FLASH, lanterns
#      SWAY) so even if one subject under-animates, the frame stays alive.
#   3) "bouncy rhythm" / "to the beat" cue — Veo recognizes music-style
#      pacing and amplifies subject motion.
# If still too static, retry with VEO_MODEL=veo-3.1-generate-preview (standard
# tier, ~$4 vs lite $0.60) — richer motion, BUT it may re-render decorations
# mid-clip. For our regen'd vtuber backgrounds that's a risk; standard tier
# only recommended for cut3 specifically.
animate_one cut3_dance_party \
  "An orange tabby cat and a small French bulldog without a tail are dancing together at a vibrant party. The cat bobs its head and swishes its tail to the beat, front paws tapping. At the same time the dog rocks side to side rhythmically, its ears bouncing, mouth slightly open in excitement. Colorful confetti swirls and falls all around them. Party lights flash and music notes float upward. Paper lanterns sway overhead. Camera holds still capturing the bouncy rhythm."

# ─── cut4: cuddle / peace (multi-subject — dual motion pattern) ────────
animate_one cut4_cuddle_peace \
  "An orange tabby cat and a small French bulldog without a tail sleep peacefully cuddled together. The cat breathes slowly — its chest rises and falls. At the same time the dog breathes calmly and a whisker twitches. Paper lanterns drift upward in the background sky. Camera holds still."

echo
echo "Done. Episode 02 clips:"
ls -lh "$OUT_DIR"/cut1_peony_greeting.mp4 \
       "$OUT_DIR"/cut2_sunbathe_meditate.mp4 \
       "$OUT_DIR"/cut3_dance_party.mp4 \
       "$OUT_DIR"/cut4_cuddle_peace.mp4 2>/dev/null || true
