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
pause_streak=0
for round in $(seq 1 "$MAX_ROUNDS"); do
  # Stay OUT of the 01:00–06:59 protected window: it covers the 01:30 icloud-sync, the
  # 03:00 launch batch, and the 3–6am Photos-maintenance / iCloud download-failure window.
  # The osxphotos lock only WAITS then proceeds WITHOUT exclusivity, so overlapping the
  # batch here re-creates the PhotoKit contention. If we enter the window mid-run, stop
  # cleanly — the 07:00 launchd job (com.rianileo.petlabel-backlog) resumes the backlog.
  hour=$(( 10#$(date +%H) ))
  if [ "$hour" -ge 1 ] && [ "$hour" -lt 7 ]; then
    echo ">>> 01:00–07:00 protected window (sync/batch/Photos-maint) — stopping; 07:00 job resumes." | tee -a "$LOG"
    break
  fi
  # Linux-portable free-GB parse. NOTE: `df -g` is a macOS flag — on Linux it errors and
  # returns EMPTY, which used to read as 0 < floor → pause 60s → `continue` every round, so
  # the job spun for hours labelling nothing (the runaway this guard prevents). Use GNU
  # `--output=avail -BG` and keep only digits.
  free_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -z "$free_gb" ] && free_gb=$(df -BG / 2>/dev/null | awk 'END{gsub(/[^0-9]/,"",$4); print $4}')
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    pause_streak=$((pause_streak + 1))
    # A broken disk parser or a genuinely wedged disk must NOT spin forever. Abort after
    # MAX_PAUSE_STREAK consecutive below-floor checks (default 10 = ~10 min) so a failure is
    # loud + bounded instead of a silent multi-hour pause loop.
    if [ "$pause_streak" -ge "${MAX_PAUSE_STREAK:-10}" ]; then
      echo ">>> disk below floor for ${pause_streak} consecutive checks (free='${free_gb}'GB) — aborting to avoid a spin loop (check df output / real disk)." | tee -a "$LOG"
      exit 3
    fi
    echo ">>> free disk ${free_gb}GB < ${MIN_FREE_GB}GB floor — pausing 60s (streak ${pause_streak}/${MAX_PAUSE_STREAK:-10})" | tee -a "$LOG"
    sleep 60; continue
  fi
  pause_streak=0
  echo "" | tee -a "$LOG"
  echo "===== ROUND $round ($(date '+%T'))  free=${free_gb}GB  batch~${BATCH_GB}GB =====" | tee -a "$LOG"
  # KEEP_DAYS=0 → prune deletes this round's batch right after it's mirrored to GCS.
  # ALLOW_FULL_EXPORT=1 + BATCH_BYTES → bypass the bootstrap guard but stay chunk-bounded.
  # ICLOUD_SKIP_PHASH=1: the backlog is archival-library coverage tagging, not RF dedup.
  # phash needs a full HEIC software-decode per photo (libheif = the real CPU bottleneck,
  # NOT the network) and isn't needed for these old photos; skip it. The daily pipeline
  # still computes phash. Backfill later with a dedicated pass if dedup ever needs it.
  out=$(ICLOUD_ALLOW_FULL_EXPORT=1 ICLOUD_BACKFILL_BATCH_BYTES="$BATCH_BYTES" \
        ICLOUD_PRUNE_KEEP_DAYS=0 ICLOUD_PRUNE_FREE_FLOOR_GB=50 ICLOUD_SKIP_PHASH=1 \
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
