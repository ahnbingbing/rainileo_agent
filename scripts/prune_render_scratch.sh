#!/usr/bin/env bash
# Prune render scratch so the VM disk doesn't hit 100% (which silently turns every
# render into a render_error and empties launch slots — PD 2026-08-21 incident).
# SAFE by construction: deletes only regenerable scratch/intermediates and final episode
# mp4s that are ALREADY on YouTube + GCS (older than EP_KEEP_DAYS) — never source assets
# (data/assets) and never a recent workdir a re-caption might reuse. Idempotent; logs freed
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

# PD 2026-08-27: final episode mp4s were the actual disk hog (18G / 600+ files) and were
# NEVER trimmed — the 02:50 prune freed only a few GB while episodes/ filled the disk to
# 100%, so the 8/29 batch produced nothing (the exact "full disk → render_error → empty
# slot" failure this script exists to prevent). Final episodes are uploaded to YouTube AND
# mirrored to GCS (gs://rianileo-assets/output/episodes/) at publish time, so a local copy
# older than a few days is redundant scratch. Trim mp4 + thumb older than EP_KEEP_DAYS
# (default 4 — well past upload/mirror). Re-caption/re-render use the cameraman WORKDIR
# (pruned above at >2d), not these assembled outputs, so this is safe.
EP_KEEP_DAYS="${EP_KEEP_DAYS:-4}"
find "$ROOT/data/output/episodes" -maxdepth 1 -type f \
     \( -name '*.mp4' -o -name '*.thumb.jpg' \) -mtime "+${EP_KEEP_DAYS}" -delete 2>/dev/null

after=$(df --output=avail -k / | tail -1)
pct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
freed=$(( (after - before) / 1024 ))
echo "$(date '+%F %T') prune_render_scratch: freed ${freed}MB, disk now ${pct}% used"
# loud warning if still tight — a fill-up should never be silent again
if [ "${pct:-0}" -ge 90 ]; then
  echo "$(date '+%F %T') ⚠️ DISK STILL ${pct}% AFTER PRUNE — episodes/ (final mp4s) may need GCS-mirror-then-trim"
fi
