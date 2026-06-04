#!/usr/bin/env bash
# scripts/generate_all_stickers.sh
# ---------------------------------
# One-shot full sticker library generation for Ryani & Leo channel.
#
# Runs all 18 sticker batches sequentially (~131 stickers total).
# Each batch is independent — if one fails the others still run.
#
# Estimated cost: ~$5.24 (gpt-image-1 medium quality, 1024x1024)
# Estimated time: ~15-20 minutes
#
# Prereqs:
#   pip install openai python-dotenv
#   .env has OPENAI_API_KEY set
#
# Run:
#   bash scripts/generate_all_stickers.sh
#
# Resume after failure: just re-run. Existing PNGs are kept (new
# timestamps prevent overwrites), so you can re-run safely to fill
# in any failed batches.

cd "$(dirname "$0")/.."

# ──────────────────────────────────────────────────────────────────
# Cleanup of the previous sequin-feeling sparkles run
# ──────────────────────────────────────────────────────────────────
echo "==================================="
echo "Ryani & Leo full sticker library"
echo "==================================="
echo "Cleaning up previous sparkles AI batch (user said too sequin-y)..."
rm -f assets/stickers/sparkles/sparkles_ai_*.png
echo "  done."
echo ""

start_ts=$(date +%s)
batch_num=0
total_batches=18

run_batch() {
    batch_num=$((batch_num + 1))
    local label="$1"
    shift
    echo ""
    echo ">>> [$batch_num/$total_batches] $label"
    python3 scripts/generate_stickers_ai.py "$@" || echo "    !! batch failed, continuing..."
}

# ──────────────────────────────────────────────────────────────────
# HEARTS — mixed + Ryani-themed + Leo-themed (18 total)
# ──────────────────────────────────────────────────────────────────
run_batch "Hearts — mixed palette (8)" \
  --category hearts --count 8 \
  --style "3D puffy glossy heart, optional soft gingham plaid pattern on some variants, kawaii cottagecore, thick crisp white outer outline, soft drop shadow"

run_batch "Hearts — Ryani theme: pink/coral/blush (5)" \
  --category hearts --count 5 --color-theme ryani \
  --style "3D puffy glossy heart, optional soft gingham plaid pattern on some variants, kawaii cottagecore, thick crisp white outer outline, soft drop shadow"

run_batch "Hearts — Leo theme: gold/butter/amber (5)" \
  --category hearts --count 5 --color-theme leo \
  --style "3D puffy glossy heart, kawaii cottagecore, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# SPARKLES — clean star compositions (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Sparkles — clean star clusters and shooting stars (8)" \
  --category sparkles --count 8 \
  --style "3D puffy glossy kawaii cottagecore, soft glow, clean and charming, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# PAWS — mixed + Ryani + Leo (14 total)
# ──────────────────────────────────────────────────────────────────
run_batch "Paws — mixed palette (6)" \
  --category paws --count 6 \
  --style "3D puffy glossy kawaii cottagecore paw print, thick crisp white outer outline, soft drop shadow"

run_batch "Paws — Ryani theme (4)" \
  --category paws --count 4 --color-theme ryani \
  --style "3D puffy glossy kawaii cottagecore paw print, thick crisp white outer outline, soft drop shadow"

run_batch "Paws — Leo theme (4)" \
  --category paws --count 4 --color-theme leo \
  --style "3D puffy glossy kawaii cottagecore paw print, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# CHARACTER FACES — Ryani and Leo (16 total)
# ──────────────────────────────────────────────────────────────────
run_batch "Ryani face — chibi French bulldog with varied expressions (8)" \
  --category ryani_face --count 8 \
  --style "3D puffy glossy kawaii chibi cottagecore character sticker, charming and adorable, thick crisp white outer outline, soft drop shadow"

run_batch "Leo face — chibi orange tabby kitten with varied expressions (8)" \
  --category leo_face --count 8 \
  --style "3D puffy glossy kawaii chibi cottagecore character sticker, charming and adorable, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# FOOD — kawaii food with facial expressions (12)
# ──────────────────────────────────────────────────────────────────
run_batch "Food — kawaii foods with varied facial expressions (12)" \
  --category food --count 12 \
  --style "3D puffy glossy kawaii cottagecore food with cute facial expression on it, smooth glossy finish, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# FACES — emotion stickers (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Faces — emotion expression stickers (8)" \
  --category faces --count 8 \
  --style "3D puffy glossy kawaii cottagecore emotion sticker, expressive and very cute, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# BUBBLES — speech bubble + icon (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Bubbles — speech bubble with simple icon inside (8)" \
  --category bubbles --count 8 \
  --style "3D puffy glossy kawaii cottagecore speech bubble, clean rounded square shape with little tail, pastel fill, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# LABELS — text phrases (14)
# ──────────────────────────────────────────────────────────────────
run_batch "Labels — 14 English cottagecore text phrases" \
  --category labels \
  --text "happy day,enjoy,good vibes,sweet day,cozy time,best day,yay!,love love,stay cute,warm hugs,purrfect,lovely,best buds,sweet moments" \
  --style "3D puffy glossy pill or ribbon banner, script or rounded sans-serif font, kawaii cottagecore aesthetic, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# WEATHER — year-round motifs (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Weather — sun, cloud, rain, rainbow, snow, moon (8)" \
  --category weather --count 8 \
  --style "3D puffy glossy weather motif with optional smiling face on sun/cloud variants for cuteness, kawaii cottagecore, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# COZY — lifestyle objects (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Cozy — candles, books, mittens, fairy lights, blankets (8)" \
  --category cozy --count 8 \
  --style "3D puffy glossy cozy lifestyle object, kawaii cottagecore aesthetic, steam wisps where appropriate, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# CUTE — flowers, bows, butterflies, clouds (8)
# ──────────────────────────────────────────────────────────────────
run_batch "Cute — cherry blossoms, daisies, bows, butterflies (8)" \
  --category cute --count 8 \
  --style "3D puffy glossy cute decoration, kawaii cottagecore aesthetic, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# CLOSING — nighttime motifs (5)
# ──────────────────────────────────────────────────────────────────
run_batch "Closing — moon, alarm clock, shooting star (5)" \
  --category closing --count 5 --color-theme cool \
  --style "3D puffy glossy nighttime motif, kawaii cute, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# MUSIC — notes (4)
# ──────────────────────────────────────────────────────────────────
run_batch "Music — notes (4)" \
  --category music --count 4 \
  --style "3D puffy glossy music note, kawaii cute, thick crisp white outer outline, soft drop shadow"

# ──────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────
end_ts=$(date +%s)
elapsed=$((end_ts - start_ts))
echo ""
echo "==================================="
echo "All batches finished."
echo "Total elapsed: $((elapsed / 60))m $((elapsed % 60))s"
echo ""
echo "Library inventory:"
for d in assets/stickers/*/; do
    cat=$(basename "$d")
    count=$(ls -1 "$d"*.png 2>/dev/null | wc -l | tr -d ' ')
    printf "  %-12s  %s PNGs\n" "$cat" "$count"
done
echo "==================================="
