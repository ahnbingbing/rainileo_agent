#!/usr/bin/env bash
# deploy/run_job.sh — uniform wrapper for every periodic job on the VM (called by
# crontab.vm). Sources app secrets + sets cwd/PYTHONPATH/TZ so cron lines stay tiny
# and DRY. First arg decides how to run:  *.py → python file, *.sh → bash, else → python -m.
#   run_job.sh -m agents.bandit --collect
#   run_job.sh scripts/slack_sync.py
#   run_job.sh scripts/petlabels_chunked.sh
set -euo pipefail

CONF=/etc/rianileo/deploy.env
# set -a so non-secret runtime config in deploy.env (e.g. BOARD_EXEC_MODE) is EXPORTED to
# the python child, not just a shell var. The secrets file is likewise exported below.
[ -f "$CONF" ] && { set -a; . "$CONF"; set +a; }
: "${APP_DIR:?set APP_DIR in $CONF}" "${PY:?set PY in $CONF}"
: "${APP_ENV_FILE:=/etc/rianileo/env}"

cd "$APP_DIR"
[ -f "$APP_ENV_FILE" ] && { set -a; . "$APP_ENV_FILE"; set +a; }
export TZ=Asia/Seoul PYTHONPATH="$APP_DIR"

case "${1:-}" in
  *.py) exec "$PY" "$@" ;;
  *.sh) exec bash "$@" ;;
  *)    exec "$PY" "$@" ;;
esac
