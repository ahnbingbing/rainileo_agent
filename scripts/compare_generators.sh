#!/bin/bash
# compare_generators.sh
# ---------------------
# Side-by-side i2v generator comparison.
#
#   A = sora-2          (current; 720x1280, ~$0.40 / 4s clip)
#   B = Veo 3 standard  (veo-3.0-generate-001, with audio, ~$2-3 / 4s clip — VERIFY)
#   C = Veo 3 Fast      (veo-3.0-fast-generate-001, cheaper, ~$0.50-1 / 4s clip — VERIFY)
#
# Runs the same input image + same prompt through all three, dumps the mp4s
# into a comparison folder so you can eyeball quality + animal motion + audio
# + cost differences, then decide whether to migrate the pipeline off sora-2.
#
# Default test asset: cut5 (the "smoking gun" — sora-2 keeps producing static
# animals even after motion-retry on this one). If Veo 3 fixes cut5
# consistently, that alone justifies the cost bump.
#
# Usage:
#   bash scripts/compare_generators.sh                          # dry run (no API)
#   bash scripts/compare_generators.sh --real                   # actually call APIs
#   bash scripts/compare_generators.sh --real --tag cut3        # use cut3 instead
#   bash scripts/compare_generators.sh --real --skip-a          # skip sora-2 (already have it)
#   bash scripts/compare_generators.sh --real --only b          # only run Veo 3 std
#
# Env:
#   OPENAI_API_KEY    required for A
#   GOOGLE_API_KEY    required for B / C
#
# Output:
#   data/output/generator_compare_<timestamp>/
#     a_sora2/<tag>.mp4 (+ meta.json)
#     b_veo3/<tag>.mp4
#     c_veo3_fast/<tag>.mp4
#     summary.txt   (cost + time per generator + motion scores)
#
# IMPORTANT: defaults to --dry-run. Adding --real WILL be billed.

set -uo pipefail
cd "$(dirname "$0")/.."

REAL=0
TAG="cut5_closer"
ONLY=""
SKIP_A=0
SKIP_B=0
SKIP_C=0
AGGRESSIVE=0
# Conservative defaults (GA models, verified 2026-05-15 via
# generativelanguage.googleapis.com/v1beta/models listing).
# Override with --aggressive to use 3.1 preview tier instead.
VEO3_MODEL="${VEO3_MODEL:-veo-3.0-generate-001}"
VEO3_FAST_MODEL="${VEO3_FAST_MODEL:-veo-3.0-fast-generate-001}"
# Cost-per-second list-price ESTIMATES (verify on Google docs before
# budgeting for real recurring runs).
COST_B_PER_SEC="${COST_B_PER_SEC:-0.75}"
COST_C_PER_SEC="${COST_C_PER_SEC:-0.25}"

while [ $# -gt 0 ]; do
  case "$1" in
    --real)        REAL=1 ;;
    --tag)         TAG="$2"; shift ;;
    --only)        ONLY="$2"; shift ;;
    --skip-a)      SKIP_A=1 ;;
    --skip-b)      SKIP_B=1 ;;
    --skip-c)      SKIP_C=1 ;;
    --aggressive|--preview|--3.1)
                   AGGRESSIVE=1 ;;
    -h|--help)     sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown: $1" >&2; exit 2 ;;
  esac
  shift
done

# Aggressive mode: swap B/C to 3.1 preview tier (latest known, may not be
# behaviorally identical to 3.0 — preview models can change without notice).
# B = 3.1 standard, C = 3.1 lite (widest cost spectrum so you see the floor + ceiling).
if [ $AGGRESSIVE -eq 1 ]; then
  VEO3_MODEL="veo-3.1-generate-preview"
  VEO3_FAST_MODEL="veo-3.1-lite-generate-preview"
  # rough estimate — lite tier is typically ~30-40% of standard
  COST_C_PER_SEC=0.15
fi

if [ -n "$ONLY" ]; then
  SKIP_A=1; SKIP_B=1; SKIP_C=1
  case "$ONLY" in
    a|A) SKIP_A=0 ;;
    b|B) SKIP_B=0 ;;
    c|C) SKIP_C=0 ;;
    *) echo "--only must be a, b, or c" >&2; exit 2 ;;
  esac
fi

IMG="data/output/decorated/${TAG}.png"
if [ ! -f "$IMG" ]; then
  echo "ERROR: input image $IMG not found" >&2
  exit 2
fi

# Use the verified C-style prompt that already works on sora-2.
# Note: Veo 3 prompts often benefit from more cinematographic language
# (dolly / push-in / handheld) — see notes/sora2_motion_lessons.md if you
# want to tune separately per generator later.
PROMPT="An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail. At the same time the dog tilts its head and blinks. Camera gently pushes in toward them."
SECS=4

RUN_TS=$(date +%Y%m%d_%H%M%S)
OUT="data/output/generator_compare_${RUN_TS}"
mkdir -p "$OUT/a_sora2" "$OUT/b_veo3" "$OUT/c_veo3_fast"

SUMMARY="$OUT/summary.txt"
{
  echo "Generator comparison run $RUN_TS"
  echo "  tag    = $TAG"
  echo "  image  = $IMG"
  echo "  prompt = $PROMPT"
  echo "  secs   = $SECS"
  echo "  mode   = $([ $REAL -eq 1 ] && echo REAL || echo DRY-RUN)$([ $AGGRESSIVE -eq 1 ] && echo "  (AGGRESSIVE — 3.1 preview)")"
  echo "  B model = $VEO3_MODEL"
  echo "  C model = $VEO3_FAST_MODEL"
  echo
  printf "%-18s | %-9s | %-8s | %-7s | %s\n" \
    "generator" "cost(est)" "time(s)" "motion" "path"
  echo "-------------------+-----------+----------+---------+------------------------------"
} > "$SUMMARY"

# sora-2 720x1280 list price (May 2025 cutoff). B/C costs come from env/flag block above.
COST_A_PER_SEC=0.10

run_one() {
  # $1=label $2=generator(a|b|c) $3=output_dir
  local label="$1" gen="$2" dir="$3"
  local out="$dir/${TAG}.mp4"
  local started ended elapsed cost motion_line motion="?"

  echo
  echo "==> [$label] generating into $out"
  started=$(date +%s)

  if [ "$gen" = "a" ]; then
    cost=$(awk -v p="$COST_A_PER_SEC" -v s="$SECS" 'BEGIN{printf "$%.2f", p*s}')
    if [ "$REAL" -eq 1 ]; then
      python3 scripts/animate_hero.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" --output "$out"
    else
      python3 scripts/animate_hero.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" --output "$out" --dry-run
    fi
  elif [ "$gen" = "b" ]; then
    cost=$(awk -v p="$COST_B_PER_SEC" -v s="$SECS" 'BEGIN{printf "$%.2f", p*s}')
    if [ "$REAL" -eq 1 ]; then
      python3 scripts/animate_hero_veo3.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" \
        --model "$VEO3_MODEL" --output "$out"
    else
      python3 scripts/animate_hero_veo3.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" \
        --model "$VEO3_MODEL" --output "$out" --dry-run
    fi
  else  # c
    cost=$(awk -v p="$COST_C_PER_SEC" -v s="$SECS" 'BEGIN{printf "$%.2f", p*s}')
    if [ "$REAL" -eq 1 ]; then
      python3 scripts/animate_hero_veo3.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" \
        --model "$VEO3_FAST_MODEL" --output "$out"
    else
      python3 scripts/animate_hero_veo3.py \
        --image "$IMG" --prompt "$PROMPT" --seconds "$SECS" \
        --model "$VEO3_FAST_MODEL" --output "$out" --dry-run
    fi
  fi
  local rc=$?
  ended=$(date +%s)
  elapsed=$((ended - started))

  if [ $rc -ne 0 ]; then
    echo "  [$label] failed (rc=$rc)"
    printf "%-18s | %-9s | %-8s | %-7s | FAILED rc=%d\n" \
      "$label" "$cost" "$elapsed" "n/a" "$rc" >> "$SUMMARY"
    return 0
  fi

  if [ -f "$out" ] && [ "$REAL" -eq 1 ]; then
    motion_line=$(bash scripts/check_motion.sh "$out" 2>/dev/null | head -1)
    motion=$(echo "$motion_line" | grep -oE "mean=[0-9.]+" | head -1 | cut -d= -f2)
    motion="${motion:-?}"
  fi

  printf "%-18s | %-9s | %-8s | %-7s | %s\n" \
    "$label" "$cost" "$elapsed" "$motion" "$out" >> "$SUMMARY"
  echo "  [$label] done in ${elapsed}s, motion=$motion"
}

if [ $SKIP_A -eq 0 ]; then run_one "A:sora-2"      a "$OUT/a_sora2";      fi
if [ $SKIP_B -eq 0 ]; then run_one "B:veo-3"       b "$OUT/b_veo3";       fi
if [ $SKIP_C -eq 0 ]; then run_one "C:veo-3-fast"  c "$OUT/c_veo3_fast";  fi

echo
echo "============================================================"
cat "$SUMMARY"
echo "============================================================"
echo
echo "Eyeball check tomorrow:"
echo "  1. Do BOTH animals visibly move in B/C (the cut5 problem)?"
echo "  2. Does Veo audio add anything for Shorts? (sora-2 has none)"
echo "  3. Is the quality gap worth the cost gap?"
echo
if [ $REAL -eq 0 ]; then
  echo "(this was a DRY-RUN — pass --real to actually call APIs)"
fi
