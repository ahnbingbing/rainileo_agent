#!/bin/bash
# scripts/run_episode_04.sh
# Episode 04 일상 — end-to-end pipeline. Video-based (no AI gen).
#
# Pipeline:
#   1) Verify Nanum Pen Script font (or fall back to Pretendard)
#   2) Extract + caption 4 clips (single ffmpeg pass per cut)
#   3) Ensure bumpers exist (build if missing)
#   4) Final assemble (intro + 4 cuts + outro + main BGM)
#
# Usage:
#   bash scripts/run_episode_04.sh
#
# Env:
#   BUMPER_MUSIC  channel theme music for bumpers
#   MAIN_BGM      ambient BGM laid over the 4 content cuts
#   FORCE=1       re-run all steps even if outputs exist
#
# Cost: $0 — no API calls. Pure ffmpeg + local pipeline.

set -euo pipefail
cd "$(dirname "$0")/.."

# Defaults — override via env
BUMPER_MUSIC="${BUMPER_MUSIC:-assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3}"
# EP04 is 일상 (chill / documentary) — gentler BGM than EP02's ambient flute.
# sonican optimistic hopeful loop fits the "warm ordinary day" vibe.
MAIN_BGM="${MAIN_BGM:-assets/bgm/sonican-optimistic-music-hopeful-loop-2-520368.mp3}"
FORCE="${FORCE:-0}"

CAPTIONED_DIR="data/output/animated_captioned"
NANUM_FONT="$HOME/Library/Fonts/NanumPen.ttf"

echo "▶ Episode 04 일상 pipeline"
echo "  bumper music : $BUMPER_MUSIC"
echo "  main bgm     : $MAIN_BGM"
echo

# ─── Step 1: font check ───────────────────────────────────────────────
echo "━━━ [1/4] Font check ━━━"
if [ -f "$NANUM_FONT" ] || [ -f "$HOME/Library/Fonts/NanumPenScript.ttf" ] || [ -f "$HOME/Library/Fonts/NanumPenScript-Regular.ttf" ]; then
  echo "  Nanum Pen Script found ✓"
else
  echo "  ⚠ Nanum Pen Script NOT found — caption will use Pretendard ExtraBold (acceptable fallback)"
  echo "    For best handwritten/marker feel:"
  echo "    brew install --cask font-nanum-pen-script"
fi
echo

# ─── Step 2: extract + caption clips ──────────────────────────────────
echo "━━━ [2/4] Extract + caption 4 clips ━━━"
if [ "$FORCE" = "1" ] || [ ! -f "$CAPTIONED_DIR/cut4_cuddle_together.mp4" ]; then
  python3 scripts/extract_clips_ep04.py
else
  echo "  (existing — skip. FORCE=1 to redo)"
fi
echo

# ─── Step 3: bumpers ─────────────────────────────────────────────────
echo "━━━ [3/4] Bumpers ━━━"
need_bumper_build=0
if [ ! -f assets/branding/outro_bumper.mp4 ]; then
  need_bumper_build=1
fi
# Also rebuild if existing bumpers lack audio (channel theme music).
if [ -f assets/branding/intro_bumper.mp4 ]; then
  if ! ffprobe -v error -select_streams a:0 -show_entries stream=codec_type \
       -of csv=p=0 assets/branding/intro_bumper.mp4 2>/dev/null | grep -q audio; then
    need_bumper_build=1
  fi
fi
if [ "$FORCE" = "1" ] || [ "$need_bumper_build" = "1" ]; then
  python3 scripts/build_bumpers.py \
    --intro-music "$BUMPER_MUSIC" \
    --outro-music "$BUMPER_MUSIC"
else
  echo "  (existing bumpers w/ audio — skip)"
fi
echo

# ─── Step 4: final assemble ───────────────────────────────────────────
echo "━━━ [4/4] Final assemble ━━━"
TS=$(date +%Y%m%d_%H%M%S)
OUT="data/output/episodes/episode_04_daily_${TS}.mp4"
python3 scripts/assemble_episode.py \
  --captions scripts/prompts/episode_04_captions.json \
  --intro-bumper assets/branding/intro_bumper.mp4 \
  --outro-bumper assets/branding/outro_bumper.mp4 \
  --music "$MAIN_BGM" \
  --out "$OUT"
echo
echo "▶ Episode 04 ready: $OUT"
echo "  open $OUT"
