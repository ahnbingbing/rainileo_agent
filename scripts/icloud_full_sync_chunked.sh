#!/usr/bin/env bash
# Chunked FULL iCloud backlog sync (PD 2026-06-14).
#
# Goal: ingest + VLM-tag the ENTIRE ~8000-item old backlog (not a sample) so the
# Writer pool stops skewing recent — WITHOUT the disk bomb. Each round downloads at
# most ~BATCH_GB worth of un-ingested originals (oldest date_added first), ingests
# them to data/assets, VLM-tags the copies, then PRUNES the originals (DB row + tags
# kept, re-downloadable by uuid). Repeat until the backlog is drained.
#
# Usage:  bash scripts/icloud_full_sync_chunked.sh            # ~10 GB batches
#         BATCH_GB=8 bash scripts/icloud_full_sync_chunked.sh
set -uo pipefail
cd "$(dirname "$0")/.."

BATCH_GB="${BATCH_GB:-10}"
BATCH_BYTES=$(( BATCH_GB * 1000000000 ))
MAX_ROUNDS="${MAX_ROUNDS:-300}"
PY="${PY:-.venv/bin/python}"
LOG="${LOG:-/tmp/icloud_full_sync_chunked.log}"
: > "$LOG"

echo "chunked full sync: ${BATCH_GB}GB/round, max ${MAX_ROUNDS} rounds, log=$LOG"
zero_streak=0
for round in $(seq 1 "$MAX_ROUNDS"); do
  echo "" | tee -a "$LOG"
  echo "===== ROUND $round  ($(date '+%T'))  batch ~${BATCH_GB}GB =====" | tee -a "$LOG"
  out=$(ICLOUD_ALLOW_FULL_EXPORT=1 ICLOUD_BACKFILL_BATCH_BYTES="$BATCH_BYTES" \
        "$PY" -m icloud.sync --backfill --download-missing --vlm --prune 2>&1)
  echo "$out" >> "$LOG"
  echo "$out" | grep -E "BACKFILL batch|imported (photos|clips|live)|prune:|NEW to download|nothing new|skipped missing" | tail -8

  # Clean drain: sync reports the backlog is empty.
  if echo "$out" | grep -q "nothing new in the album"; then
    echo ">>> backlog drained (nothing new). DONE after $round rounds." | tee -a "$LOG"
    break
  fi
  # Remaining un-ingested = the 'of N un-ingested' from the batch line.
  remaining=$(echo "$out" | grep -oE "of [0-9]+ un-ingested" | tail -1 | grep -oE "[0-9]+")
  imp=$(echo "$out" | grep -oE "imported (photos|clips): *[0-9]+" \
        | grep -oE "[0-9]+$" | paste -sd+ - | bc 2>/dev/null || echo 0)
  echo ">>> round $round: imported=$imp, remaining_backlog=${remaining:-?}" | tee -a "$LOG"

  if [ "${imp:-0}" -eq 0 ]; then
    zero_streak=$((zero_streak+1))
    echo ">>> zero-import streak=$zero_streak (downloads may be throttling)" | tee -a "$LOG"
    [ "$zero_streak" -ge 4 ] && { echo ">>> 4 empty rounds — aborting (investigate)." | tee -a "$LOG"; break; }
    sleep 30
  else
    zero_streak=0
  fi
done
echo "chunked full sync finished. full log: $LOG"
