#!/usr/bin/env bash
# deploy/smoke.sh — fast pre-deploy gate. Run against a candidate checkout BEFORE it
# replaces the live code, so a broken push never reaches the always-on bot.
# Exit 0 = safe to deploy; non-zero = block (keep old code running).
#
# Usage: smoke.sh <checkout_dir>   (defaults to $SMOKE_DIR or cwd)
set -uo pipefail

DIR="${1:-${SMOKE_DIR:-$PWD}}"
PY="${PY:-$DIR/.venv/bin/python}"
cd "$DIR" || { echo "smoke: cannot cd $DIR"; exit 2; }

echo "smoke: $(git rev-parse --short HEAD) in $DIR"

# 1) Syntax: compile every tracked .py (catches the typo that would crash a job).
if ! git ls-files '*.py' -z | xargs -0 "$PY" -m py_compile; then
  echo "smoke: FAIL — py_compile"; exit 1
fi

# 2) Import the load-bearing modules (catches import-time errors / bad refactors).
#    slack.app constructs the Bolt App at module level (needs tokens from the app
#    env), but does NOT open the socket — that's under __main__. So importing is safe
#    and proves the bot process would at least start. Source app env if present.
[ -n "${APP_ENV_FILE:-}" ] && [ -f "$APP_ENV_FILE" ] && set -a && . "$APP_ENV_FILE" && set +a
if ! "$PY" - <<'PYEOF'
import importlib, sys
mods = [
    "db.init_db", "icloud.gcs", "agents.progress_log",
    "agents.producer", "agents.cameraman", "agents.launch_selfheal",
    "agents.reviewer", "agents.bandit", "scripts.process_board_escalations",
    "slack.app",
]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {e}")
if bad:
    print("import failures:"); [print("  -", b) for b in bad]; sys.exit(1)
print(f"smoke: imported {len(mods)} modules OK")
PYEOF
then
  echo "smoke: FAIL — import"; exit 1
fi

echo "smoke: PASS"
