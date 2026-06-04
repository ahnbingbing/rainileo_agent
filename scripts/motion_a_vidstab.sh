#!/bin/bash
# motion_a_vidstab.sh
# -------------------
# Method A: camera-compensated motion via ffmpeg vidstab.
#
# Theory: vidstab estimates the global 2D affine transform between frames
# (translation + rotation + scale). After stabilization, only RESIDUAL
# motion (= subject motion + small artifacts) remains. Measure YAVG on
# the stabilized clip — if subjects are static, residual ≈ 0.
#
# Usage:
#   bash scripts/motion_a_vidstab.sh path/to/clip.mp4 [threshold]
#
# Output:
#   <basename>: raw_mean=<X> stab_mean=<X> threshold=<X>  → OK|STATIC
#
# Exit codes:
#   0 = stabilized motion >= threshold (subjects moved)
#   1 = stabilized motion <  threshold (likely camera-only)
#   2 = error

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <mp4> [threshold]" >&2
  exit 2
fi

MP4="$1"
THRESH="${2:-${MIN_MOTION_A:-1.0}}"  # lower than YAVG raw, since residual is smaller

if [ ! -f "$MP4" ]; then
  echo "ERROR: $MP4 not found" >&2
  exit 2
fi

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

# pass 1: detect transforms
ffmpeg -nostats -loglevel error -y -i "$MP4" \
  -vf "vidstabdetect=stepsize=4:shakiness=10:accuracy=15:result=$TMP/t.trf" \
  -f null - 2>/dev/null || { echo "ERROR: vidstabdetect failed on $MP4" >&2; exit 2; }

# pass 2: stabilize (smoothing high so slow camera moves get compensated)
ffmpeg -nostats -loglevel error -y -i "$MP4" \
  -vf "vidstabtransform=input=$TMP/t.trf:smoothing=30:crop=keep:zoom=0:optzoom=2:interpol=linear" \
  -c:v libx264 -preset ultrafast -crf 23 -an \
  "$TMP/stab.mp4" 2>/dev/null || { echo "ERROR: vidstabtransform failed on $MP4" >&2; exit 2; }

# raw mean for reference
raw_vals=$(ffmpeg -nostats -loglevel info -i "$MP4" \
  -vf "tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG" \
  -f null - 2>&1 | grep -oE "YAVG=[0-9.]+" | cut -d= -f2 || true)
raw_mean=$(echo "$raw_vals" | awk '{s+=$1;n++}END{if(n>0)printf "%.3f",s/n;else print "0"}')

# residual mean on stabilized clip
stab_vals=$(ffmpeg -nostats -loglevel info -i "$TMP/stab.mp4" \
  -vf "tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG" \
  -f null - 2>&1 | grep -oE "YAVG=[0-9.]+" | cut -d= -f2 || true)

if [ -z "$stab_vals" ]; then
  echo "ERROR: no YAVG from stabilized clip" >&2
  exit 2
fi

stab_mean=$(echo "$stab_vals" | awk '{s+=$1;n++}END{if(n>0)printf "%.3f",s/n;else print "0"}')

verdict="STATIC"
awk -v m="$stab_mean" -v t="$THRESH" 'BEGIN{exit (m<t)?1:0}' && verdict="OK"

printf "%s: raw_mean=%s stab_mean=%s threshold=%s  → %s\n" \
  "$(basename "$MP4")" "$raw_mean" "$stab_mean" "$THRESH" "$verdict"

[ "$verdict" = "OK" ] && exit 0 || exit 1
