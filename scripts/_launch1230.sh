#!/bin/bash
# One-shot launcher for the 8/19 12:30 re-render, invoked over a SHORT ssh command
# (`sudo bash scripts/_launch1230.sh`) because the flaky IAP tunnel drops longer inline
# `systemd-run ...` commands. Kills any duplicate render, then launches exactly one under
# a timeout guard. Env is baked into the render script itself.
pkill -9 -f _render_av_0819 2>/dev/null
pkill -9 -f animate_seedance 2>/dev/null
systemctl reset-failed avfin1230 2>/dev/null
systemd-run --uid=rianileo --gid=rianileo -p RuntimeMaxSec=1600 \
  --working-directory=/home/rianileo/rianileo-agent --unit=avfin1230 --collect \
  bash deploy/run_job.sh scripts/_render_av_0819_1230.py
echo "launched: $(systemctl is-active avfin1230)"
