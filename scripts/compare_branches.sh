#!/usr/bin/env bash
# PD 2026-06-04: render 1 real_footage episode per branch (A/B/C) on the
# same date, for storytelling-quality comparison vs 쿠들습격 gold standard.
set -uo pipefail

DATE="${1:-2026-05-22}"
LOG_DIR="/tmp/branch_compare"
mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR/results.txt"

render_branch() {
  local key="$1" branch="$2"
  local log="$LOG_DIR/branch_$key.log"
  echo "[$(date +%H:%M:%S)] -- Branch $key ($branch) --"
  git checkout -q "$branch" || { echo "checkout failed for $branch"; return; }
  OPENAI_FALLBACK_MODEL=gpt-5-mini CAPTION_JUDGE_MODEL=gpt-5-mini \
    WRITER_MODEL=claude-opus-4-7 DIRECTOR_MODEL=claude-opus-4-7 \
    .venv/bin/python -m agents.producer --date "$DATE" --style real_footage --no-slack \
    > "$log" 2>&1
  local newest
  newest=$(ls -t data/output/episodes/episode_rf_*.mp4 2>/dev/null | head -1)
  echo "[$(date +%H:%M:%S)] Branch $key -> $newest"
  echo "$key|$branch|$newest" >> "$LOG_DIR/results.txt"
  echo
}

echo "[$(date +%H:%M:%S)] Comparing branches on date $DATE"
echo
render_branch A approach-a-rollback
render_branch B approach-b-flowing-narrator
render_branch C approach-c-strip-constraints

echo "[$(date +%H:%M:%S)] All 3 rendered. Results:"
cat "$LOG_DIR/results.txt" 2>/dev/null
