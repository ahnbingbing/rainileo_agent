#!/usr/bin/env bash
# deploy/pull_deploy.sh — the "git push = deploy" engine. Runs on the VM every ~2 min
# (rianileo-deploy.timer). Polls origin/main; when it moves, smoke-tests the new commit
# in an ISOLATED clone, and only if smoke passes advances the live tree + restarts the
# always-on bot. A broken push is blocked — the live bot keeps running the last good code.
#
# Cron/timer jobs (launch, producer, board-escalations, …) spawn a fresh python each
# fire, so they pick up new code automatically — only the long-lived bot needs a restart.
set -uo pipefail

CONF="${1:-/etc/rianileo/deploy.env}"
[ -f "$CONF" ] && . "$CONF"
: "${APP_DIR:?}" "${SMOKE_DIR:?}" "${PY:?}"
: "${DEPLOY_REMOTE:=origin}" "${DEPLOY_BRANCH:=main}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Single-flight: never let two deploy runs overlap.
exec 9>/tmp/rianileo-deploy.lock
flock -n 9 || { echo "deploy: another run holds the lock — skip"; exit 0; }

log()    { echo "[$(date '+%F %T')] $*"; }
notify() {  # best-effort Slack ping; never fails the deploy
  [ "${DEPLOY_SLACK_NOTIFY:-0}" = "1" ] || return 0
  ( cd "$APP_DIR" && [ -f "${APP_ENV_FILE:-}" ] && set -a && . "$APP_ENV_FILE" && set +a
    "$PY" -c "import sys;from agents.progress_log import log_progress;log_progress('deploy',sys.argv[1])" "$1" ) 2>/dev/null || true
}

cd "$APP_DIR" || { log "cannot cd $APP_DIR"; exit 2; }
git fetch --quiet "$DEPLOY_REMOTE" "$DEPLOY_BRANCH" || { log "fetch failed"; exit 0; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "$DEPLOY_REMOTE/$DEPLOY_BRANCH")
[ "$LOCAL" = "$REMOTE" ] && exit 0          # nothing new — the common case, stay quiet
log "new commit: ${LOCAL:0:7} → ${REMOTE:0:7}"

# Smoke the candidate in the isolated clone (never touches the live tree).
if [ ! -d "$SMOKE_DIR/.git" ]; then
  git clone --quiet "$(git remote get-url "$DEPLOY_REMOTE")" "$SMOKE_DIR" || { log "smoke clone failed"; exit 0; }
fi
( cd "$SMOKE_DIR" && git fetch --quiet "$DEPLOY_REMOTE" "$DEPLOY_BRANCH" && git reset --quiet --hard "$DEPLOY_REMOTE/$DEPLOY_BRANCH" )
if ! APP_ENV_FILE="${APP_ENV_FILE:-}" PY="$PY" SMOKE_DIR="$SMOKE_DIR" bash "$HERE/smoke.sh" "$SMOKE_DIR"; then
  log "DEPLOY BLOCKED — smoke failed at ${REMOTE:0:7} (live keeps ${LOCAL:0:7})"
  notify ":no_entry: deploy BLOCKED — smoke 실패 ${REMOTE:0:7}, 라이브는 ${LOCAL:0:7} 유지"
  exit 0
fi

# Smoke passed → advance live (deploy-only tree, no manual edits → hard reset is safe).
git reset --quiet --hard "$REMOTE" || { log "live reset failed"; exit 1; }

# Reinstall deps only if requirements moved (keeps the common deploy fast).
if ! git diff --quiet "$LOCAL" "$REMOTE" -- requirements.txt; then
  log "requirements.txt changed — pip install"
  "$PY" -m pip install -q -r requirements.txt || log "pip install warned (continuing)"
fi

# Restart the always-on bot so it runs the new code. Timers/cron pick it up on next fire.
SYSTEMCTL="systemctl"; [ "$(id -u)" != 0 ] && SYSTEMCTL="sudo systemctl"   # sudoers NOPASSWD rule
$SYSTEMCTL restart rianileo-bot.service && log "restarted rianileo-bot"
log "DEPLOYED ${REMOTE:0:7}"
notify ":rocket: deployed ${REMOTE:0:7} → 봇 재시작"
