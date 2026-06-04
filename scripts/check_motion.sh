#!/bin/bash
# check_motion.sh
# ---------------
# Computes a motion score for an mp4 clip and exits 0 (sufficient motion)
# or 1 (too static). Used by animate_all_cuts.sh to detect sora-2 "frozen"
# outputs and auto-retry.
#
# Algorithm:
#   ffmpeg "tblend=all_mode=difference" produces a stream where each frame
#   is the absolute difference between consecutive input frames. Then
#   "signalstats" reports per-frame YAVG (average luminance of that
#   diff frame, 0~255). We average YAVG over the whole clip.
#
#   ~0 = consecutive frames are nearly identical (very static).
#   >1.5 = clearly some motion (subject and/or camera).
#   >2.5 = strong motion.
#
# Calibration data (2026-05-13, 720x1280 sora-2 4s clips):
#   "정말 움직임 없음" examples:  0.36 ~ 0.66
#   "스티커만 움직임":           0.53 ~ 0.66
#   사용자 OK examples:           1.80 ~ 2.18
#   5/11 검증 head-rub:           1.88
#   abc cut5_c (위너):            2.73
#   5/11 push-in (드라마틱):       7.82
#
#   → default threshold 1.5 separates cleanly.
#
# Usage:
#   bash scripts/check_motion.sh path/to/clip.mp4 [threshold]
# Prints:
#   "<basename>: mean=<x.xxx> sd=<x.xxx> max=<x.xxx>"
# Exit codes:
#   0 = motion >= threshold (good)
#   1 = motion <  threshold (too static, candidate for retry)
#   2 = error (file missing, ffmpeg fail, no frames)

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <mp4_path> [threshold]" >&2
  exit 2
fi

MP4="$1"
THRESH="${2:-${MIN_MOTION:-1.5}}"

if [ ! -f "$MP4" ]; then
  echo "ERROR: $MP4 not found" >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERROR: ffmpeg not on PATH" >&2
  exit 2
fi

vals=$(ffmpeg -nostats -loglevel info -i "$MP4" \
  -vf "tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG" \
  -f null - 2>&1 | grep -oE "YAVG=[0-9.]+" | cut -d= -f2 || true)

if [ -z "$vals" ]; then
  echo "ERROR: no YAVG samples extracted from $MP4 (corrupt or zero-length?)" >&2
  exit 2
fi

# compute mean / stddev / max via awk
stats=$(echo "$vals" | awk '
  {a[NR]=$1; s+=$1; if (NR==1||$1<mn) mn=$1; if (NR==1||$1>mx) mx=$1}
  END {
    if (NR == 0) { print "0 0 0"; exit }
    m=s/NR
    for (i=1;i<=NR;i++) v+=(a[i]-m)^2
    sd=sqrt(v/NR)
    printf "%.3f %.3f %.3f", m, sd, mx
  }')

mean=$(echo "$stats" | awk '{print $1}')
sd=$(echo "$stats" | awk '{print $2}')
mx=$(echo "$stats" | awk '{print $3}')

printf "%s: mean=%s sd=%s max=%s threshold=%s\n" \
  "$(basename "$MP4")" "$mean" "$sd" "$mx" "$THRESH"

# verdict
awk -v m="$mean" -v t="$THRESH" 'BEGIN{exit (m < t) ? 1 : 0}'
