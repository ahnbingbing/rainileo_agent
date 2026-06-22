#!/bin/bash
# One-shot re-VLM of pre-adoption footage (PD 2026-06-22, scheduled 07:10 KST).
#
# Clears the bug-2 residue: old clips whose scene_description prose still names a
# stray cat "레오" (Leo born ~2025-09) or labels a cat as the dog Ryani. Re-tags the
# pre-2025 cat-mentioning rows with the date-aware grounding prompt, then self-disables
# so it runs exactly once. (Kicked off interactively 6/22 but PD chose to run it at 7am.)
#
# Idempotent two ways: a done-marker short-circuits any later fire, and on success the
# job unloads + removes its own plist. Re-enable by re-copying the plist + deleting the
# marker.
set -uo pipefail
cd /Users/ahnbingbing/code/rianileo-agent || exit 1

MARKER="data/logs/.revlm_pre2025_done"
PLIST="$HOME/Library/LaunchAgents/com.rianileo.revlm-pre2025.plist"
LOG="data/logs/revlm_pre2025_residue.log"

if [ -f "$MARKER" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  exit 0
fi

{
  echo "=== re-VLM pre-2025 residue START $(date '+%F %T %Z') ==="
  .venv/bin/python scripts/tag_assets_vlm.py --force --captured-before 2025-09-25 \
    --scene-like 'leo,레오,고양이,cat,tabby,오렌지,주황' --workers 6
  rc=$?
  echo "=== re-VLM pre-2025 residue END rc=$rc $(date '+%F %T %Z') ==="
} >> "$LOG" 2>&1

if [ "${rc:-1}" -eq 0 ]; then
  touch "$MARKER"
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST" 2>/dev/null || true
fi
exit "${rc:-1}"
