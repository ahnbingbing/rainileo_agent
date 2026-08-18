#!/bin/bash
# One-shot launcher for the 8/20 08:00 AV re-render (레오 반나절 가출), invoked over a
# SHORT ssh command (`sudo bash scripts/_launch0820_0800.sh`) because the flaky IAP tunnel
# drops longer inline `systemd-run ...` commands. Kills any duplicate render, then launches
# exactly one under a timeout guard. Env is baked into the render script itself.
pkill -9 -f _render_av_0820 2>/dev/null
pkill -9 -f animate_seedance 2>/dev/null
systemctl reset-failed avfin0820 2>/dev/null
systemd-run --uid=rianileo --gid=rianileo -p RuntimeMaxSec=3300 \
  --working-directory=/home/rianileo/rianileo-agent --unit=avfin0820 --collect \
  bash deploy/run_job.sh scripts/_render_av_0820_0800.py
echo "launched: $(systemctl is-active avfin0820)"
