#!/bin/bash
# scripts/run_episode_03.sh
# Episode 03 Ryani판 — end-to-end pipeline. Run on Mac (needs Gemini API,
# Pretendard font, gcloud-free since we're using Veo 3.1 lite via API key).
#
# Pipeline:
#   1) Preprocess  (sandbox already did this — skipped if files exist)
#   2) AI regen    (4 cuts, vtuber-style → 수묵화)
#   3) Veo i2v     (4 cuts, gentle motion)
#   4) Build bumpers (with channel-theme music baked in)
#   5) Burn captions (Pretendard, 수묵화-mood KO+EN)
#   6) Final assemble (intro_bumper + 4 cuts + outro_bumper + main BGM)
#
# Usage:
#   bash scripts/run_episode_03.sh
#
# Env:
#   BUMPER_MUSIC  channel theme baked into intro+outro bumpers
#   MAIN_BGM      main bgm laid over the 4 content cuts
#   FORCE=1       re-run all steps even if intermediate outputs exist
#
# Each step is idempotent (checks for existing output, skips unless FORCE=1).
# If a step fails, fix the issue and re-run — earlier completed steps won't
# repeat unless their outputs are deleted.

set -euo pipefail
cd "$(dirname "$0")/.."

# Auto-load .env so GOOGLE_API_KEY (and other secrets) are available without
# the user remembering to `set -a; source .env; set +a` each session.
# `set -a` exports anything that gets assigned in the sourced script.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Defaults — override via env
BUMPER_MUSIC="${BUMPER_MUSIC:-assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3}"
MAIN_BGM="${MAIN_BGM:-assets/bgm/kuzu420-ambient-electronic-flute-bgm-431329.mp3}"
FORCE="${FORCE:-0}"

if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "ERROR: GOOGLE_API_KEY not set. Add to .env or export it before running." >&2
  exit 2
fi

INPUT_DIR="data/tmp/episode_03_input"
REGEN_DIR="data/tmp/episode_03_regen"
ANIM_DIR="data/output/animated"
CAPTIONED_DIR="data/output/animated_captioned"

if [ ! -f "$BUMPER_MUSIC" ]; then
  echo "ERROR: BUMPER_MUSIC not found: $BUMPER_MUSIC" >&2
  echo "Set BUMPER_MUSIC=path/to/track.mp3 or check existing files in assets/bgm/" >&2
  exit 2
fi
if [ ! -f "$MAIN_BGM" ]; then
  echo "ERROR: MAIN_BGM not found: $MAIN_BGM" >&2
  exit 2
fi

echo "▶ Episode 03 Ryani판 pipeline"
echo "  bumper music : $BUMPER_MUSIC"
echo "  main bgm     : $MAIN_BGM"
echo

# ─── Step 1: Preprocess (sandbox usually pre-completed) ───────────────
echo "━━━ [1/6] Preprocess source photos ━━━"
if [ "$FORCE" = "1" ] || [ ! -f "$INPUT_DIR/cut4_ryani_peaceful.jpg" ]; then
  python3 scripts/preprocess_for_i2v.py \
    --manifest scripts/prompts/episode_03_sources.json \
    --out-dir "$INPUT_DIR/"
else
  echo "  (existing — skip. FORCE=1 to redo)"
fi
echo

# ─── Step 2: AI regen (Gemini 2.5 Flash Image) ────────────────────────
echo "━━━ [2/6] AI regen — 수묵화 톤 변환 ━━━"
if [ "$FORCE" = "1" ] || [ ! -f "$REGEN_DIR/cut4_ryani_peaceful.png" ]; then
  python3 scripts/regen_vtuber_style.py \
    --prompts scripts/prompts/episode_03_regen_prompts.json \
    --in-dir "$INPUT_DIR/" \
    --out-dir "$REGEN_DIR/"
  echo "  ▷ verify the 4 regen'd PNGs visually:"
  echo "    open $REGEN_DIR/cut*.png"
  echo "  if Ryani's white markings are wrong on any cut, re-run that cut alone:"
  echo "    python3 scripts/regen_vtuber_style.py --cut cut1_ryani_greeting \\"
  echo "      --prompts scripts/prompts/episode_03_regen_prompts.json \\"
  echo "      --in-dir $INPUT_DIR/ --out-dir $REGEN_DIR/ --n 3"
else
  echo "  (existing — skip)"
fi
echo

# ─── Step 3: Veo i2v ──────────────────────────────────────────────────
echo "━━━ [3/6] Veo i2v — gentle 수묵화 motion ━━━"
bash scripts/animate_episode_03.sh
echo

# ─── Step 4: Build bumpers (with channel theme audio baked in) ────────
echo "━━━ [4/6] Build intro/outro bumpers ━━━"
if [ "$FORCE" = "1" ] || [ ! -f assets/branding/outro_bumper.mp4 ] \
   || ! ffprobe -v error -select_streams a:0 -show_entries stream=codec_type \
        -of csv=p=0 assets/branding/intro_bumper.mp4 2>/dev/null | grep -q audio; then
  python3 scripts/build_bumpers.py \
    --intro-music "$BUMPER_MUSIC" \
    --outro-music "$BUMPER_MUSIC"
else
  echo "  (existing bumpers w/ audio — skip. FORCE=1 to rebuild)"
fi
echo

# ─── Step 5: Burn captions (Pretendard + 수묵화-mood text) ────────────
echo "━━━ [5/6] Burn captions ━━━"
if [ "$FORCE" = "1" ] || [ ! -f "$CAPTIONED_DIR/cut4_ryani_peaceful.mp4" ]; then
  python3 scripts/burn_captions.py \
    --manifest scripts/prompts/episode_03_captions.json
else
  echo "  (existing — skip)"
fi
echo

# ─── Step 6: Final assemble ───────────────────────────────────────────
echo "━━━ [6/6] Assemble episode ━━━"
TS=$(date +%Y%m%d_%H%M%S)
OUT="data/output/episodes/episode_03_ryani_${TS}.mp4"
python3 scripts/assemble_episode.py \
  --captions scripts/prompts/episode_03_captions.json \
  --intro-bumper assets/branding/intro_bumper.mp4 \
  --outro-bumper assets/branding/outro_bumper.mp4 \
  --music "$MAIN_BGM" \
  --out "$OUT"
echo
echo "▶ Episode 03 ready: $OUT"
echo "  open $OUT"
