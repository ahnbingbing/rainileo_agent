#!/bin/bash
# Giri review-learning pass — fires every 3 days, FULL AUTO (PD 2026-06-21, temporary
# "until stabilization"). Runs headless Claude Code on this Mac with full repo + .env + DB
# access — a cloud agent can't reach the local Slack/DB/working-tree this needs. Edits are
# left UNCOMMITTED + summarized to the board so PD can veto/revert. Disable with:
#   launchctl bootout gui/$(id -u)/com.rianileo.giri-weekly
set -u
cd /Users/ahnbingbing/code/rianileo-agent || exit 1

CLAUDE=/Users/ahnbingbing/.local/bin/claude
PROMPT_FILE=notes/giri_weekly_review_learning_prompt.md
LOG=data/logs/giri_weekly_$(date +%Y%m%d).log

echo "=== Giri weekly review-learning start $(date) ===" >> "$LOG"
# --print: headless. --permission-mode/skip: autonomous edits (own machine, scoped by prompt).
"$CLAUDE" --print --permission-mode acceptEdits --dangerously-skip-permissions \
  "$(cat "$PROMPT_FILE")" >> "$LOG" 2>&1
echo "=== Giri weekly review-learning end $(date) (rc=$?) ===" >> "$LOG"
