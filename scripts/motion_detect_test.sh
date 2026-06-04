#!/bin/bash
# motion_detect_test.sh
# ---------------------
# Runs all 3 motion-detection methods (A=vidstab residual,
# B=VLM frame-pair, C=YAVG+VLM hybrid) against a CURATED list of
# clips with known GOOD/BAD/BORDERLINE labels, and prints a comparison
# table. Lets us pick which method to wire into animate_all_cuts.sh.
#
# Curated labels come from §10 of notes/session_log_20260513.md and
# the user's manual review on 2026-05-13:
#
#   GOOD     animals genuinely moved (pose change visible end-to-end)
#   BAD      animals static (camera/stickers may still move)
#   ?        borderline / not yet user-judged
#
# Usage:
#   bash scripts/motion_detect_test.sh                      # full suite
#   bash scripts/motion_detect_test.sh --skip-b             # skip VLM (no API cost)
#   bash scripts/motion_detect_test.sh --skip-b --skip-c    # only Method A
#   bash scripts/motion_detect_test.sh --only PATTERN       # only clips matching grep
#
# Cost preview: ~12 clips × (B + C) ≈ 24 VLM calls ≈ $0.24-0.48 worst case.
# (C will skip its VLM half on clips that fail stage-1, so usually cheaper.)
#
# Output:
#   one row per clip with: expected | A verdict | B verdict | C verdict
#   plus a per-method accuracy summary at the end.

set -uo pipefail

cd "$(dirname "$0")/.."

SKIP_A=0
SKIP_B=0
SKIP_C=0
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-a) SKIP_A=1 ;;
    --skip-b) SKIP_B=1 ;;
    --skip-c) SKIP_C=1 ;;
    --only)   ONLY="$2"; shift ;;
    -h|--help)
      sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

ANIM=data/output/animated

# Curated test set: "label|relative_path|note"
# label: GOOD | BAD | ?
declare -a CASES=(
  # ===== GOOD =====
  "GOOD|${ANIM}/med_2026_05_06_203421_icloud_331110de__20260511_221327.mp4|5/11 head-rub, animal motion verified by user"
  "GOOD|${ANIM}/med_2026_05_06_203433_icloud_57e3500d__20260511_221910.mp4|5/11 strong push-in + animal motion"
  "GOOD|${ANIM}/abc_test/cut5_c_dual_pushin.mp4|A/B/C winner (variant C), user picked"
  "GOOD|${ANIM}/cut1_ryani_hook.mp4|current prod cut1, passed motion + not flagged"
  "GOOD|${ANIM}/cut2_leo_intro.mp4|current prod cut2, passed motion + not flagged"
  "GOOD|${ANIM}/cut4_together_warm.mp4|current prod cut4, passed motion + not flagged"

  # ===== BAD (animals static; some have moving stickers / camera) =====
  # THE SMOKING GUN: live cut5_closer.mp4 (latest run) — passes naive YAVG
  # AND passes Method A (stab_mean=1.800) yet user rejected it because the
  # animals don't move; only camera push-in + stickers create motion.
  "BAD|${ANIM}/cut3_together_play.mp4|live (latest) — user rejected, stickers move not animals"
  "BAD|${ANIM}/cut5_closer.mp4|live (latest) — SMOKING GUN: passes A but animals frozen"
  "BAD|${ANIM}/_archive_static_user_flagged_20260513/cut3_together_play.mp4|earlier user-flagged round"
  "BAD|${ANIM}/_archive_static_user_flagged_20260513/cut5_closer.mp4|earlier user-flagged round"
  "BAD|${ANIM}/_archive_pre_pushin_20260513/cut3_together_play.mp4|pre-pushin baseline static"
  "BAD|${ANIM}/_archive_pre_pushin_20260513/cut5_closer.mp4|pre-pushin baseline static"
  "BAD|${ANIM}/_archive_static_20260513_222627/cut3_together_play_attempt1.mp4|static-archived during retry run"
  "BAD|${ANIM}/_archive_static_20260513_222627/cut5_closer_attempt1.mp4|static-archived during retry run"

  # ===== BORDERLINE (no user verdict yet) =====
  "?|${ANIM}/abc_test/cut5_a_baseline.mp4|A/B/C variant A (baseline)"
  "?|${ANIM}/abc_test/cut5_b_compressed.mp4|A/B/C variant B (compressed)"
)

# print header
hdr_fmt="%-5s | %-8s | %-8s | %-8s | %-40s | %s\n"
row_fmt="%-5s | %-8s | %-8s | %-8s | %-40s | %s\n"

echo
printf "$hdr_fmt" "EXP" "A:vstab" "B:vlm" "C:hybrid" "clip" "note"
printf "%s\n" "------+----------+----------+----------+------------------------------------------+---------------------------"

# tallies — only count rows where expected is GOOD or BAD
declare -A method_correct
declare -A method_wrong
declare -A method_err
for m in A B C; do
  method_correct[$m]=0
  method_wrong[$m]=0
  method_err[$m]=0
done

# raw log dir
RUN_TS=$(date +%Y%m%d_%H%M%S)
LOG_DIR="data/output/motion_detect_test_${RUN_TS}"
mkdir -p "$LOG_DIR"
echo "Detail logs → $LOG_DIR"
echo

run_method() {
  # $1 method (A|B|C), $2 clip path, $3 expected label
  local m="$1" clip="$2" exp="$3"
  local logf="$LOG_DIR/$(basename "$clip" .mp4).${m}.log"
  local rc verdict
  case "$m" in
    A)
      bash scripts/motion_a_vidstab.sh "$clip" >"$logf" 2>&1
      rc=$?
      ;;
    B)
      python3 scripts/motion_b_vlm.py "$clip" >"$logf" 2>&1
      rc=$?
      ;;
    C)
      bash scripts/motion_c_hybrid.sh "$clip" >"$logf" 2>&1
      rc=$?
      ;;
  esac

  case "$rc" in
    0) verdict="OK" ;;
    1) verdict="STATIC" ;;
    2) verdict="ERROR" ;;
    *) verdict="rc=$rc" ;;
  esac

  echo "$verdict|$rc"
}

tally() {
  local m="$1" verdict="$2" exp="$3"
  if [ "$verdict" = "SKIP" ] || [ "$verdict" = "MISS" ]; then
    return  # don't tally — method wasn't actually run on this clip
  fi
  if [ "$verdict" = "ERROR" ]; then
    method_err[$m]=$((method_err[$m] + 1))
    return
  fi
  case "$exp" in
    GOOD)
      if [ "$verdict" = "OK" ]; then method_correct[$m]=$((method_correct[$m] + 1));
      else method_wrong[$m]=$((method_wrong[$m] + 1)); fi
      ;;
    BAD)
      if [ "$verdict" = "STATIC" ]; then method_correct[$m]=$((method_correct[$m] + 1));
      else method_wrong[$m]=$((method_wrong[$m] + 1)); fi
      ;;
    "?")
      : ;;  # don't tally
  esac
}

for entry in "${CASES[@]}"; do
  IFS='|' read -r exp clip note <<< "$entry"

  if [ -n "$ONLY" ] && ! echo "$clip" | grep -q -- "$ONLY"; then
    continue
  fi

  short=$(echo "$clip" | sed -E "s|^${ANIM}/||")
  short="${short:0:40}"

  if [ ! -f "$clip" ]; then
    printf "$row_fmt" "$exp" "MISS" "MISS" "MISS" "$short" "($note)"
    continue
  fi

  if [ $SKIP_A -eq 0 ]; then
    res=$(run_method A "$clip" "$exp"); a_verdict="${res%%|*}"
  else
    a_verdict="SKIP"
  fi
  if [ $SKIP_B -eq 0 ]; then
    res=$(run_method B "$clip" "$exp"); b_verdict="${res%%|*}"
  else
    b_verdict="SKIP"
  fi
  if [ $SKIP_C -eq 0 ]; then
    res=$(run_method C "$clip" "$exp"); c_verdict="${res%%|*}"
  else
    c_verdict="SKIP"
  fi

  printf "$row_fmt" "$exp" "$a_verdict" "$b_verdict" "$c_verdict" "$short" "$note"

  tally A "$a_verdict" "$exp"
  tally B "$b_verdict" "$exp"
  tally C "$c_verdict" "$exp"
done

echo
echo "========== ACCURACY (excluding '?' borderline clips) =========="
for m in A B C; do
  c=${method_correct[$m]}
  w=${method_wrong[$m]}
  e=${method_err[$m]}
  total=$((c + w))
  if [ $total -eq 0 ]; then
    pct="-"
  else
    pct=$(awk -v a="$c" -v b="$total" 'BEGIN{printf "%.0f%%", 100*a/b}')
  fi
  echo "  Method $m : correct=$c wrong=$w errors=$e  → $pct"
done
echo
echo "Detail logs: $LOG_DIR/<clipname>.<method>.log"
echo "Pick the winner by accuracy first, then by cost (A free / B paid / C mostly free)."
