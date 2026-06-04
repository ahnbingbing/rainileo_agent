#!/bin/bash
# decorate_all_cuts.sh
# Episode 1 — image-only pipeline.
# Decorate all 5 cuts via gpt-image-1 edit + per-cut prompt files.
#
# Usage:
#   bash scripts/decorate_all_cuts.sh           # default: medium quality (~$0.20)
#   bash scripts/decorate_all_cuts.sh high      # high quality (~$0.85)
#
# Outputs: data/output/decorated/cut{1..5}_<recipe>.png  (1080x1920)

set -euo pipefail

cd "$(dirname "$0")/.."
QUALITY="${1:-medium}"

TMP_DIR=/tmp/rianileo_decorate
mkdir -p "$TMP_DIR"

# Helper — convert HEIC to JPEG via sips (mac) / heif-convert / ffmpeg.
heic_to_jpg() {
  local src="$1" out="$2"
  if command -v sips >/dev/null 2>&1; then
    sips -s format jpeg "$src" --out "$out" >/dev/null
  elif command -v heif-convert >/dev/null 2>&1; then
    heif-convert "$src" "$out" >/dev/null
  else
    ffmpeg -y -i "$src" -q:v 2 "$out" >/dev/null 2>&1
  fi
  echo "$out"
}

echo "==> Preparing source images (HEIC -> JPEG where needed)..."
CUT1_IMG=data/assets/photos/2026/med_2026_05_06_203421_icloud_331110de.jpeg
CUT2_IMG=data/assets/photos/2026/med_2026_05_06_203433_icloud_57e3500d.jpeg
CUT3_IMG=$(heic_to_jpg data/assets/photos/2025/med_2025_11_21_112556_icloud_11fe4ba7.heic "$TMP_DIR/cut3.jpg")
CUT4_IMG=$(heic_to_jpg data/assets/photos/2025/med_2025_12_14_152858_icloud_7fb8be27.heic "$TMP_DIR/cut4.jpg")
CUT5_IMG=$(heic_to_jpg data/assets/photos/2025/med_2025_12_12_193926_icloud_6a1268c0.heic "$TMP_DIR/cut5.jpg")

for v in "$CUT1_IMG" "$CUT2_IMG" "$CUT3_IMG" "$CUT4_IMG" "$CUT5_IMG"; do
  [ -f "$v" ] || { echo "MISSING source: $v"; exit 2; }
  echo "  ok  $v"
done

decorate_one() {
  local img="$1" prompt="$2" tag="$3"
  echo "==> Decorating $tag (quality=$QUALITY)"
  python3 scripts/decorate_raw.py \
    --image "$img" \
    --prompt-file "$prompt" \
    --quality "$QUALITY" \
    --out "data/output/decorated/${tag}.png"
}

decorate_one "$CUT1_IMG" scripts/prompts/edit_v3_cut1_ryani_hook.txt     cut1_ryani_hook
decorate_one "$CUT2_IMG" scripts/prompts/edit_v3_cut2_leo_intro.txt      cut2_leo_intro
decorate_one "$CUT3_IMG" scripts/prompts/edit_v3_cut3_together_play.txt  cut3_together_play
decorate_one "$CUT4_IMG" scripts/prompts/edit_v3_cut4_together_warm.txt  cut4_together_warm
decorate_one "$CUT5_IMG" scripts/prompts/edit_v3_cut5_closer.txt         cut5_closer

echo
echo "Done. Decorated PNGs:"
ls -lh data/output/decorated/cut*.png
