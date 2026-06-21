#!/usr/bin/env bash
# Pet-photo coverage backlog, CHUNKED with strict disk control (PD 2026-06-21:
# "디스크 재확보하고 10기가씩만 받고 작업 삭제 다시 10기가씩만").
#
# Ingests pet-labeled photos from across the WHOLE library (개/고양이/불도그/…), not just
# the album, so album-omitted pet photos stop being missed. Each round:
#   download ≤BATCH_GB → ingest → VLM tag (subjects: keeps Leo/Ryani usable) →
#   mirror to GCS → prune local (KEEP_DAYS=0, deletes the batch) → next round
# So local disk never holds more than ~1 batch. Peak ≈ 2×BATCH_GB (export dir + the
# copy into data/assets before prune), so size BATCH_GB to your free disk: keep
# 2×BATCH_GB well under free space. Default 4GB (≈8GB peak).
#
# Idempotent/resumable: already-ingested uuids are skipped, so re-running continues.
#   BATCH_GB=4 bash scripts/petlabels_chunked.sh
set -uo pipefail
cd "$(dirname "$0")/.."

BATCH_GB="${BATCH_GB:-4}"
BATCH_BYTES=$(( BATCH_GB * 1000000000 ))
MAX_ROUNDS="${MAX_ROUNDS:-400}"
MIN_FREE_GB="${MIN_FREE_GB:-3}"          # abort a round if free disk would dip under this
PY="${PY:-.venv/bin/python}"
LOG="${LOG:-data/logs/petlabels_chunked_$(date +%Y%m%d).log}"
: > "$LOG"

echo "pet-label backlog: ${BATCH_GB}GB/round (≈$((BATCH_GB*2))GB peak), max ${MAX_ROUNDS} rounds, log=$LOG"
zero_streak=0
for round in $(seq 1 "$MAX_ROUNDS"); do
  free_gb=$(df -g / | tail -1 | awk '{print $4}')
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    echo ">>> free disk ${free_gb}GB < ${MIN_FREE_GB}GB floor — pausing 60s to let prune/system catch up" | tee -a "$LOG"
    sleep 60; continue
  fi
  echo "" | tee -a "$LOG"
  echo "===== ROUND $round ($(date '+%T'))  free=${free_gb}GB  batch~${BATCH_GB}GB =====" | tee -a "$LOG"
  # KEEP_DAYS=0 → prune deletes this round's batch right after it's mirrored to GCS.
  # ALLOW_FULL_EXPORT=1 + BATCH_BYTES → bypass the bootstrap guard but stay chunk-bounded.
  out=$(ICLOUD_ALLOW_FULL_EXPORT=1 ICLOUD_BACKFILL_BATCH_BYTES="$BATCH_BYTES" \
        ICLOUD_PRUNE_KEEP_DAYS=0 ICLOUD_PRUNE_FREE_FLOOR_GB=50 \
        "$PY" -m icloud.sync --pet-labels --backfill --download-missing --vlm --prune 2>&1)
  echo "$out" >> "$LOG"
  echo "$out" | grep -E "label-select|BACKFILL batch|imported (photos|clips)|GCS mirror|prune:|NEW to download|nothing new" | tail -8

  if echo "$out" | grep -qE "nothing new|no photos match"; then
    echo ">>> pet-label backlog drained. DONE after $round rounds." | tee -a "$LOG"; break
  fi
  imp=$(echo "$out" | grep -oE "imported (photos|clips): *[0-9]+" | grep -oE "[0-9]+$" | paste -sd+ - | bc 2>/dev/null || echo 0)
  echo ">>> round $round: imported=$imp" | tee -a "$LOG"
  if [ "${imp:-0}" -eq 0 ]; then
    zero_streak=$((zero_streak+1))
    [ "$zero_streak" -ge 4 ] && { echo ">>> 4 empty rounds — aborting (investigate log)." | tee -a "$LOG"; break; }
    sleep 30
  else
    zero_streak=0
  fi
done
echo "pet-label backlog finished. full log: $LOG"
