#!/bin/bash
# animate_all_cuts.sh
# Episode 1 — i2v pass over the 5 decorated PNGs → 5 short mp4 clips.
#
# Usage:
#   bash scripts/animate_all_cuts.sh                      # Veo 3.1 lite (~$3 total) ← DEFAULT, A/B/C winner
#   bash scripts/animate_all_cuts.sh --generator sora     # sora-2 720x1280 (~$2 total, legacy fallback)
#   bash scripts/animate_all_cuts.sh --generator sora pro # sora-2-pro 1080x1920 (~$10 total)
#   bash scripts/animate_all_cuts.sh \
#        --veo-model veo-3.1-generate-preview             # Veo 3.1 standard (~$15, beware sticker rewrites)
#
# Generator notes (2026-05-15 full-episode validation; see notes/sora2_motion_lessons.md §6-7):
#   veo   : Veo 3.1 lite preview ($0.60/clip) — DEFAULT.
#           Preserves decorated stickers, animates both animals reliably on
#           single-subject cuts. Multi-subject cuts can have one subject
#           static (typical: smaller / darker subject), but at Shorts pacing
#           it's acceptable. Veo 3.1 standard generates richer motion BUT
#           re-renders the sticker layer mid-clip, so avoid unless stickers
#           are intentionally baked separately or overlaid post-hoc.
#   sora  : cheapest ($0.40/clip) but "animals frozen" reproduces on cut5.
#           Kept as legacy fallback via --generator sora.
#
# Outputs: data/output/animated/cut{1..5}_<recipe>.mp4   (4s each)
#
# Env vars (2026-05-13 motion-retry patch):
#   MIN_MOTION    motion score threshold (default 1.5).
#                 If generated clip's mean YAVG diff is below this, the clip
#                 is treated as "too static" and regenerated (generators are
#                 stochastic — same prompt can yield different motion).
#   MAX_RETRIES   retries per cut on static output (default 2). Worst-case
#                 cost per cut = (1 + MAX_RETRIES) * <per-clip price>.
#   MOD_RETRIES   free moderation retries (default 2).
#   SKIP_MOTION_CHECK=1  bypass the motion check entirely (legacy behavior).
#   VEO_MODEL     override default veo model (default veo-3.1-lite-generate-preview).
#
# Calibration / scoring details: see scripts/check_motion.sh header.

set -euo pipefail

cd "$(dirname "$0")/.."

# ---- arg parse ---------------------------------------------------------
GENERATOR="veo"           # sora | veo  (default flipped to veo on 2026-05-15 after full-ep validation)
MODE="test"               # test | pro  (only applies to sora)
VEO_MODEL_FLAG=""         # only used when --veo-model is passed
while [ $# -gt 0 ]; do
  case "$1" in
    --generator)  GENERATOR="$2"; shift ;;
    --veo-model)  VEO_MODEL_FLAG="$2"; shift ;;
    pro|--pro)    MODE="pro" ;;
    test|--test)  MODE="test" ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

# ---- generator settings -----------------------------------------------
PRO_FLAG=""
case "$GENERATOR" in
  sora)
    SIZE_LABEL="720x1280"
    COST_NOTE="~\$2 total"
    if [ "$MODE" = "pro" ]; then
      PRO_FLAG="--pro"; SIZE_LABEL="1080x1920"; COST_NOTE="~\$10 total"
    fi
    ;;
  veo)
    # A/B/C winner: 3.1 lite preserves the decorated-PNG composition AND
    # animates both animals. Override with --veo-model or VEO_MODEL env.
    VEO_MODEL="${VEO_MODEL_FLAG:-${VEO_MODEL:-veo-3.1-lite-generate-preview}}"
    SIZE_LABEL="720x1280 9:16 ($VEO_MODEL)"
    # rough estimate; verify against ai.dev pricing for budgeting
    if [[ "$VEO_MODEL" == *lite* ]]; then
      COST_NOTE="~\$3 total"
    elif [[ "$VEO_MODEL" == *fast* ]]; then
      COST_NOTE="~\$5 total"
    else
      COST_NOTE="~\$15 total"
    fi
    ;;
  *)
    echo "unknown generator: $GENERATOR (use sora or veo)" >&2; exit 2
    ;;
esac

SECS=4
DEC_DIR=data/output/decorated
ANIM_DIR=data/output/animated
mkdir -p "$ANIM_DIR"

MIN_MOTION="${MIN_MOTION:-1.5}"
MAX_RETRIES="${MAX_RETRIES:-2}"
MOD_RETRIES="${MOD_RETRIES:-2}"
SKIP_MOTION_CHECK="${SKIP_MOTION_CHECK:-0}"

# timestamp for any static-retry archive folder this run creates
STATIC_ARCHIVE_DIR="${ANIM_DIR}/_archive_static_$(date +%Y%m%d_%H%M%S)"

echo "==> Animating 5 cuts via ${GENERATOR} (${SIZE_LABEL}, ${SECS}s each, ${COST_NOTE})"
if [ "$SKIP_MOTION_CHECK" != "1" ]; then
  echo "==> motion check: threshold=${MIN_MOTION}, max retries=${MAX_RETRIES} per cut"
fi
echo "==> moderation retry: up to ${MOD_RETRIES} extra free attempts on mod_blocked"
echo

FAILED_TAGS=()
STATIC_RETRY_NOTES=()
MOD_RETRY_NOTES=()

# Returns 0 if the sidecar JSON for the given mp4 path indicates a
# moderation_blocked status (i.e. the failure was a *free* moderation reject,
# not a paid timeout / API error). Reads ${mp4}.meta.json — written by
# animate_hero.py on every call.
sidecar_says_moderation_blocked() {
  local out="$1"
  local meta="${out}.meta.json"
  [ -f "$meta" ] || return 1
  # match either an explicit "code": "moderation_blocked" field or the
  # phrase appearing anywhere in the error message.
  grep -q -E '"(code|error)"[[:space:]]*:[[:space:]]*"[^"]*moderation_blocked' "$meta" 2>/dev/null && return 0
  grep -q "moderation_blocked" "$meta" 2>/dev/null && return 0
  return 1
}

animate_one() {
  local tag="$1" motion="$2"
  local img="${DEC_DIR}/${tag}.png"
  local out="${ANIM_DIR}/${tag}.mp4"
  if [ ! -f "$img" ]; then
    echo "MISSING $img — run decorate_all_cuts.sh first"
    FAILED_TAGS+=("$tag (missing source)")
    return 0
  fi
  if [ -f "$out" ]; then
    echo "==> ${tag}  (already exists, skipping — delete to re-run)"
    return 0
  fi

  local attempt=1
  local mod_attempt=0
  local max_attempts=$((MAX_RETRIES + 1))
  while [ $attempt -le $max_attempts ]; do
    if [ $attempt -eq 1 ]; then
      echo "==> ${tag}"
    else
      echo "==> ${tag}  retry ${attempt}/${max_attempts} (previous attempt too static)"
    fi
    # tolerate single-cut failures so the rest still run
    set +e
    if [ "$GENERATOR" = "sora" ]; then
      python3 scripts/animate_hero.py \
        --image "$img" \
        --prompt "$motion" \
        --seconds "$SECS" \
        $PRO_FLAG \
        --output "$out"
    else
      # veo
      python3 scripts/animate_hero_veo3.py \
        --image "$img" \
        --prompt "$motion" \
        --seconds "$SECS" \
        --model "$VEO_MODEL" \
        --output "$out"
    fi
    local status=$?
    set -e
    if [ $status -ne 0 ]; then
      # Distinguish moderation_blocked (free, stochastic → retry) from other
      # failures (paid timeout, hard API error → move on, same prompt would
      # likely fail again). Reads the sidecar JSON written by animate_hero.py.
      if sidecar_says_moderation_blocked "$out" && [ $mod_attempt -lt $MOD_RETRIES ]; then
        mod_attempt=$((mod_attempt + 1))
        echo "  moderation_blocked (free) — retry ${mod_attempt}/${MOD_RETRIES}"
        MOD_RETRY_NOTES+=("$tag mod-retry ${mod_attempt}")
        # archive the failed sidecar so we don't confuse the next attempt
        if [ -f "${out}.meta.json" ]; then
          mkdir -p "$STATIC_ARCHIVE_DIR"
          mv "${out}.meta.json" "$STATIC_ARCHIVE_DIR/${tag}_mod_attempt${mod_attempt}.meta.json"
        fi
        continue
      fi
      echo "  !! ${tag} failed (exit ${status}) — continuing with next cut"
      FAILED_TAGS+=("$tag (gen-fail attempt ${attempt})")
      echo
      return 0
    fi

    # generated successfully — now check motion
    if [ "$SKIP_MOTION_CHECK" = "1" ]; then
      echo "  (motion check skipped — SKIP_MOTION_CHECK=1)"
      echo
      return 0
    fi

    set +e
    bash scripts/check_motion.sh "$out" "$MIN_MOTION"
    local motion_status=$?
    set -e

    if [ $motion_status -eq 0 ]; then
      echo "  motion OK"
      echo
      return 0
    elif [ $motion_status -eq 2 ]; then
      echo "  !! motion check errored — keeping clip, not retrying"
      FAILED_TAGS+=("$tag (motion-check error attempt ${attempt})")
      echo
      return 0
    fi

    # too static
    if [ $attempt -lt $max_attempts ]; then
      mkdir -p "$STATIC_ARCHIVE_DIR"
      mv "$out" "$STATIC_ARCHIVE_DIR/${tag}_attempt${attempt}.mp4"
      if [ -f "${out}.meta.json" ]; then
        mv "${out}.meta.json" "$STATIC_ARCHIVE_DIR/${tag}_attempt${attempt}.mp4.meta.json"
      fi
      echo "  archived static attempt to ${STATIC_ARCHIVE_DIR}/"
      STATIC_RETRY_NOTES+=("$tag attempt ${attempt} archived")
      attempt=$((attempt + 1))
      continue
    else
      echo "  !! exhausted retries (${MAX_RETRIES}) — keeping last clip anyway"
      FAILED_TAGS+=("$tag (still-static after ${MAX_RETRIES} retries)")
      echo
      return 0
    fi
  done
}

# Motion prompts — rules of thumb (updated 2026-05-13 after A/B/C cut5 test):
#   * VERIFIED PATTERN (2026-05-13 winner): dual motion + camera push-in.
#       "An {animal_A} and a {animal_B} sit side by side.
#        The {animal_A} slowly {action_A}.
#        At the same time the {animal_B} {action_B}.
#        Camera gently pushes in toward them."
#     See notes/proven_motion_prompts.json entry i2v_2026_05_13_cut5_c_winner.
#   * If a cat is in the frame, the tail MUST swish (mandatory primary motion).
#   * Describe BOTH animals when both are present, with VARIED small motions
#     (ears, blink, head tilt, breathing) — otherwise Sora freezes one of them.
#   * Use "At the same time" to enforce simultaneous motion on both subjects.
#   * Avoid proper nouns (Leo / Ryani) — Sora moderation blocks them
#     (non-deterministic; safer to avoid in auto operations).
#   * Avoid "warp / animate / morph" verbs — also blocked.
#   * Avoid em-dashes in the motion prompt — they sometimes confuse the parser.
#   * Camera: explicit push-in ("Camera gently pushes in toward them.") works
#     well. Use "Camera holds still." only if a fixed frame is intended.
#   * Do NOT tell stickers to stay still — Sora over-applies that to the whole
#     frame and freezes everything.
#   * Do NOT stack emphasis phrases ("throughout the entire clip" +
#     "continuously" + "clearly visible the whole time" + "from start to
#     finish" all at once). Multiple stacked emphases trip Sora's text
#     moderation as suspected prompt-injection — keep prompts plain and
#     describe the motion once, not three times. One short modifier
#     ("slowly", "gently") is enough.

animate_one cut1_ryani_hook \
  "An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail and flicks an ear. At the same time the dog tilts its head and blinks once. Camera gently pushes in toward them."

animate_one cut2_leo_intro \
  "An orange tabby cat and a small black French bulldog sit side by side. The dog tilts its head and twitches its ears. At the same time the cat slowly swishes its tail. Camera gently pushes in toward them."

animate_one cut3_together_play \
  "An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail and blinks. At the same time the dog tilts its head and its ears twitch. Camera gently pushes in toward them."

animate_one cut4_together_warm \
  "An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail and its whiskers twitch. At the same time the dog breathes calmly and blinks. Camera gently pushes in toward them."

animate_one cut5_closer \
  "An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail. At the same time the dog tilts its head and blinks. Camera gently pushes in toward them."

echo
echo "Done. Clips:"
ls -lh "$ANIM_DIR"/*.mp4 2>/dev/null || echo "  (no clips produced)"

if [ "$SKIP_MOTION_CHECK" != "1" ]; then
  echo
  echo "Motion scores (threshold=${MIN_MOTION}):"
  for f in "$ANIM_DIR"/cut*.mp4; do
    [ -f "$f" ] || continue
    bash scripts/check_motion.sh "$f" "$MIN_MOTION" || true
  done
fi

if [ ${#STATIC_RETRY_NOTES[@]} -gt 0 ]; then
  echo
  echo "Static-retry archive: ${STATIC_ARCHIVE_DIR}"
  for n in "${STATIC_RETRY_NOTES[@]}"; do
    echo "  - $n"
  done
fi

if [ ${#MOD_RETRY_NOTES[@]} -gt 0 ]; then
  echo
  echo "Moderation retries (free, stochastic):"
  for n in "${MOD_RETRY_NOTES[@]}"; do
    echo "  - $n"
  done
fi

if [ ${#FAILED_TAGS[@]} -gt 0 ]; then
  echo
  echo "Failed cuts:"
  for t in "${FAILED_TAGS[@]}"; do
    echo "  - $t"
  done
  echo
  echo "Re-run the same script to retry only the failed cuts."
fi
