#!/usr/bin/env bash
# Prune render scratch so the VM disk doesn't hit 100% (which silently turns every
# render into a render_error and empties launch slots — PD 2026-08-21 incident).
# SAFE by construction: only deletes regenerable scratch/intermediates, never source
# assets (data/assets) or final episodes (data/output/episodes). Idempotent; logs freed
# space + disk% so a fill-up is visible in the log instead of surfacing as a mystery
# empty slot. Wire via crontab.vm (daily, after the 03:00 batch).
set -u
ROOT="/home/rianileo/rianileo-agent"
before=$(df --output=avail -k / | tail -1)

# per-render working dirs older than 2 days (a finished render never needs its workdir)
find "$ROOT/data/tmp" -maxdepth 1 -type d -name 'cameraman_*' -mtime +2 -exec rm -rf {} + 2>/dev/null
# VLM frame scratch older than 1 day
find "$ROOT/data/tmp/vlm_frames" -mindepth 1 -mtime +1 -delete 2>/dev/null
# raw seedance cuts (intermediate) older than 2 days
find "$ROOT/data/output/seedance_raw" -mindepth 1 -mtime +2 -delete 2>/dev/null
# stray hand-render leftovers in /tmp older than 1 day
find /tmp -maxdepth 1 -type f \( -name '*.mov' -o -name '*.mp4' \) -mtime +1 -delete 2>/dev/null

after=$(df --output=avail -k / | tail -1)
pct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
freed=$(( (after - before) / 1024 ))
echo "$(date '+%F %T') prune_render_scratch: freed ${freed}MB, disk now ${pct}% used"
# loud warning if still tight — a fill-up should never be silent again
if [ "${pct:-0}" -ge 90 ]; then
  echo "$(date '+%F %T') ⚠️ DISK STILL ${pct}% AFTER PRUNE — episodes/ (final mp4s) may need GCS-mirror-then-trim"
fi
