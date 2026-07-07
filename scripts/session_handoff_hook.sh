#!/usr/bin/env bash
# SessionStart hook — auto-inject session-start context into a fresh CLI session:
#   1) the latest notes/session_handoff_*.md (full text)
#   2) board↔CLI shared progress log + open board_escalations (CLAUDE.md 공유 루프)
# Wired in .claude/settings.json under hooks.SessionStart. Fail-safe: any part that
# breaks is skipped; a missing handoff exits silently (0).
set -uo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"

latest="$(ls -t "$repo"/notes/session_handoff_*.md 2>/dev/null | head -1 || true)"
[ -z "$latest" ] && exit 0

# Shared-loop context (progress log + open escalations). Best-effort; empty on failure.
py="$repo/.venv/bin/python"; [ -x "$py" ] || py="python3"
loop="$("$py" "$repo/scripts/session_context.py" 2>/dev/null || true)"

jq -Rs --arg path "${latest#$repo/}" --arg loop "$loop" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      "세션 시작 자동 핸드오프 — 최신 " + $path
      + " (아래는 그 전문; 세션은 board가 한 일 위에서 이어가라):\n\n" + .
      + (if ($loop | length) > 0 then "\n\n---\n\n" + $loop else "" end)
    )
  }
}' "$latest"
