#!/bin/bash
# motion_c_hybrid.sh
# ------------------
# Method C: cheap-first hybrid.
#   stage 1: check_motion.sh (YAVG diff) with a LOOSER threshold (1.0)
#            - if a clip is well below 1.0, it's almost certainly frozen
#              (animals + camera + stickers all static) → bail STATIC for $0.
#   stage 2: motion_b_vlm.py — if stage 1 passes, ask the VLM "did BOTH
#            animals actually change pose?" The VLM call costs ~$0.01-0.02
#            but only fires on clips that pass the cheap pre-filter, so the
#            average cost per check stays low.
#
# Rationale: pure YAVG misses "stickers/camera move, animals don't" failures
# (the cut3/cut5 problem). Pure VLM costs money on every clip. Hybrid skips
# the VLM bill on the obvious freezes.
#
# Usage:
#   bash scripts/motion_c_hybrid.sh path/to/clip.mp4
#
# Env vars:
#   MIN_MOTION_C_PIXEL   stage-1 YAVG threshold (default 1.0; lower than the
#                        production 1.5 so we only catch *clearly* dead clips)
#   VLM_MODE             passed to motion_b_vlm.py — "both" (default) or "either"
#
# Output:
#   "<basename>: stage1=<PASS|FAIL@x.xxx> stage2=<...>  → OK|STATIC"
#
# Exit codes:
#   0 OK     (both stages passed)
#   1 STATIC (stage 1 or stage 2 said no)
#   2 ERROR

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <mp4>" >&2
  exit 2
fi

MP4="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STAGE1_THRESH="${MIN_MOTION_C_PIXEL:-1.0}"
VLM_MODE="${VLM_MODE:-both}"

if [ ! -f "$MP4" ]; then
  echo "ERROR: $MP4 not found" >&2
  exit 2
fi

# stage 1: cheap YAVG screen
set +e
stage1_line=$(bash "$SCRIPT_DIR/check_motion.sh" "$MP4" "$STAGE1_THRESH" 2>&1)
stage1_status=$?
set -e

# pull mean= value out of "name: mean=X sd=Y max=Z threshold=T"
stage1_mean=$(echo "$stage1_line" | grep -oE "mean=[0-9.]+" | head -1 | cut -d= -f2)
stage1_mean="${stage1_mean:-?}"

base=$(basename "$MP4")

if [ "$stage1_status" -eq 2 ]; then
  echo "$base: stage1=ERROR ($stage1_line)" >&2
  exit 2
fi

if [ "$stage1_status" -eq 1 ]; then
  # too static even at the looser threshold → don't bother spending VLM tokens
  printf "%s: stage1=FAIL@%s stage2=skipped  → STATIC\n" "$base" "$stage1_mean"
  exit 1
fi

# stage 2: VLM
set +e
stage2_out=$(python3 "$SCRIPT_DIR/motion_b_vlm.py" "$MP4" --mode "$VLM_MODE" 2>&1)
stage2_status=$?
set -e

# motion_b_vlm.py first line: "<file>: cat=... dog=... ... → OK|STATIC"
stage2_first=$(echo "$stage2_out" | head -1)
stage2_summary=$(echo "$stage2_first" | sed -E 's/^[^:]+:[[:space:]]*//')

if [ "$stage2_status" -eq 2 ]; then
  echo "$base: stage1=PASS@$stage1_mean stage2=ERROR" >&2
  echo "$stage2_out" >&2
  exit 2
fi

if [ "$stage2_status" -eq 1 ]; then
  printf "%s: stage1=PASS@%s stage2=FAIL (%s)  → STATIC\n" \
    "$base" "$stage1_mean" "$stage2_summary"
  # surface the VLM evidence lines for debugging
  echo "$stage2_out" | tail -n +2 | sed 's/^/  /'
  exit 1
fi

printf "%s: stage1=PASS@%s stage2=PASS (%s)  → OK\n" \
  "$base" "$stage1_mean" "$stage2_summary"
echo "$stage2_out" | tail -n +2 | sed 's/^/  /'
exit 0
