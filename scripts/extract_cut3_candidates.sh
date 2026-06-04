#!/bin/bash
# extract_cut3_candidates.sh
# Dump candidate frames from the cut3 source video every 0.5s, so we can
# visually pick the most "Ryani-looking" frame before re-decorating.
#
# Usage:
#   bash scripts/extract_cut3_candidates.sh           # step = 0.5s
#   bash scripts/extract_cut3_candidates.sh 0.25      # finer step

set -euo pipefail
cd "$(dirname "$0")/.."

VID=data/assets/clips/2025/med_2025_12_14_152903_icloud_ad7fb05a.mov
OUT=/tmp/rianileo_cut3_candidates
STEP="${1:-0.5}"

mkdir -p "$OUT"
rm -f "$OUT"/frame_*.jpg

if [ ! -f "$VID" ]; then
  echo "MISSING video: $VID"
  exit 2
fi

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VID")
echo "Video duration: ${DUR}s   step=${STEP}s"
echo "Writing candidates to: $OUT"
echo

i=0
t=0.0
while [ "$(awk -v t="$t" -v d="$DUR" 'BEGIN{print (t<d)?1:0}')" = "1" ]; do
  fname=$(printf "%s/frame_%02d_t%.2fs.jpg" "$OUT" "$i" "$t")
  ffmpeg -y -ss "$t" -i "$VID" -frames:v 1 -q:v 2 "$fname" >/dev/null 2>&1
  printf "  %s\n" "$fname"
  i=$((i+1))
  t=$(awk -v t="$t" -v s="$STEP" 'BEGIN{printf "%.2f", t+s}')
done

echo
echo "Open the folder in Finder:"
echo "  open $OUT"
echo
echo "Once you pick the best frame, re-decorate with:"
cat <<'TIP'
  python3 scripts/decorate_raw.py \
    --image /tmp/rianileo_cut3_candidates/frame_XX_tYY.YYs.jpg \
    --prompt-file scripts/prompts/edit_v3_cut3_together_play.txt \
    --quality medium \
    --out data/output/decorated/cut3_together_play.png
TIP
