#!/usr/bin/env bash
# Launch 9 real_footage episodes — one per editing concept (a-i).
# PD 2026-06-02: editing-diversity A/B test.
#
# Usage: bash scripts/test_all_editing_concepts.sh [YYYY-MM-DD]
#   default date = today.
#
# Each run is sequential (avoids DB / API rate-limit races).
# Output: data/output/episodes/episode_rf_*.mp4 — 9 files to compare.
# Logs:   /tmp/edit_concept_test/<slug>.log

set -euo pipefail

TARGET_DATE="${1:-$(date +%Y-%m-%d)}"
LOG_DIR="/tmp/edit_concept_test"
mkdir -p "$LOG_DIR"

CONCEPTS=(
  rapid_montage
  long_take
  twist_ending
  themed_compilation
  photo_i2v
  split_screen
  slow_mo
  before_after
  cross_cutting
)

echo "[$(date +%H:%M:%S)] Target date: $TARGET_DATE"
echo "[$(date +%H:%M:%S)] 9 concepts: ${CONCEPTS[*]}"
echo "[$(date +%H:%M:%S)] Logs: $LOG_DIR/<slug>.log"
echo

for slug in "${CONCEPTS[@]}"; do
  ts=$(date +%H:%M:%S)
  log="$LOG_DIR/$slug.log"
  echo "[$ts] ▶ launching: $slug → $log"

  FORCE_EDITING_CONCEPT="$slug" \
  OPENAI_FALLBACK_MODEL="${OPENAI_FALLBACK_MODEL:-gpt-5-mini}" \
  CAPTION_JUDGE_MODEL="${CAPTION_JUDGE_MODEL:-gpt-5-mini}" \
  .venv/bin/python -m agents.producer \
    --date "$TARGET_DATE" \
    --style real_footage \
    --no-slack \
    > "$log" 2>&1 \
    && echo "[$(date +%H:%M:%S)] ✓ done: $slug" \
    || echo "[$(date +%H:%M:%S)] ✗ FAILED: $slug (see $log)"
done

echo
echo "[$(date +%H:%M:%S)] Episode files:"
ls -lt data/output/episodes/episode_rf_*.mp4 2>/dev/null | head -10
