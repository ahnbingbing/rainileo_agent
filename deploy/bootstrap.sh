#!/usr/bin/env bash
# deploy/bootstrap.sh — one-time VM provisioning (Debian/Ubuntu on GCE e2-medium).
# Idempotent: safe to re-run. Run as root (or via sudo) on a FRESH VM.
#
#   sudo DEPLOY_REPO=https://github.com/ahnbingbing/rainileo_agent bash bootstrap.sh
#
# What it does NOT do: provision the VM/bucket (PD does that, paid), or write secrets
# (pulled from Secret Manager — see step 6). After this, "git push to main" deploys.
set -euo pipefail

RIANILEO_USER="${RIANILEO_USER:-rianileo}"
APP_DIR="${APP_DIR:-/home/$RIANILEO_USER/rianileo-agent}"
SMOKE_DIR="${SMOKE_DIR:-/home/$RIANILEO_USER/rianileo-smoke}"
DEPLOY_REPO="${DEPLOY_REPO:?set DEPLOY_REPO=https://github.com/<owner>/<repo>}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
SECRET_NAME="${SECRET_NAME:-rianileo-env}"     # Secret Manager secret holding the .env body
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== 1. system tz + packages =="
timedatectl set-timezone Asia/Seoul || true
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip ffmpeg \
    fonts-nanum fontconfig curl jq

echo "== 2. fonts (Pretendard + Nanum Pen for burned captions) =="
install -d /usr/share/fonts/rianileo
# Nanum comes from fonts-nanum; Pretendard fetched once. (Caption tofu fix = full path
# fontfile=, same as the Mac — see CLAUDE.md gotcha #1.)
if [ ! -f /usr/share/fonts/rianileo/Pretendard-Bold.otf ]; then
  curl -fsSL -o /tmp/pretendard.zip \
    https://github.com/orioncactus/pretendard/releases/latest/download/Pretendard.zip || true
  if [ -f /tmp/pretendard.zip ]; then
    (cd /tmp && rm -rf pretendard && mkdir pretendard && cd pretendard && \
     unzip -oq /tmp/pretendard.zip && find . -name '*.otf' -exec cp {} /usr/share/fonts/rianileo/ \;) || true
  fi
fi
fc-cache -f >/dev/null 2>&1 || true

echo "== 3. service user =="
id -u "$RIANILEO_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$RIANILEO_USER"

echo "== 4. clone repo + smoke clone =="
sudo -u "$RIANILEO_USER" bash -c "
  [ -d '$APP_DIR/.git' ]   || git clone --branch '$DEPLOY_BRANCH' '$DEPLOY_REPO' '$APP_DIR'
  [ -d '$SMOKE_DIR/.git' ] || git clone --branch '$DEPLOY_BRANCH' '$DEPLOY_REPO' '$SMOKE_DIR'
  cd '$APP_DIR' && git checkout '$DEPLOY_BRANCH' && git pull --ff-only
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
  ./.venv/bin/pip install -q -r requirements.txt  # smoke clone shares the live venv via PY path
  mkdir -p data/logs
"
# smoke clone reuses the live venv (PY points at $APP_DIR/.venv) — no second venv needed.

echo "== 5. deploy config =="
install -d /etc/rianileo
[ -f /etc/rianileo/deploy.env ] || sed \
  -e "s#^APP_DIR=.*#APP_DIR=$APP_DIR#" \
  -e "s#^SMOKE_DIR=.*#SMOKE_DIR=$SMOKE_DIR#" \
  -e "s#^PY=.*#PY=$APP_DIR/.venv/bin/python#" \
  -e "s#^RIANILEO_USER=.*#RIANILEO_USER=$RIANILEO_USER#" \
  "$APP_DIR/deploy/config.env.example" > /etc/rianileo/deploy.env

echo "== 6. secrets from Secret Manager → /etc/rianileo/env =="
# The .env body (SLACK_BOT_TOKEN, SLACK_APP_TOKEN, GOOGLE_API_KEY, ANTHROPIC_API_KEY,
# OPENAI_API_KEY, BYTEPLUS_*, GCS_ASSET_BUCKET, GCP_PROJECT, …) lives in Secret Manager.
if command -v gcloud >/dev/null 2>&1; then
  if gcloud secrets versions access latest --secret="$SECRET_NAME" > /etc/rianileo/env 2>/dev/null; then
    chmod 600 /etc/rianileo/env; chown root:root /etc/rianileo/env
    echo "   secrets written"
  else
    echo "   !! could not read secret '$SECRET_NAME' — create it, then re-run step 6"
  fi
else
  echo "   !! gcloud not present — install Cloud SDK or hand-place /etc/rianileo/env (chmod 600)"
fi

echo "== 7. systemd units (bot + deploy timer) =="
cp "$HERE/systemd/rianileo-bot.service"    /etc/systemd/system/
cp "$HERE/systemd/rianileo-deploy.service" /etc/systemd/system/
cp "$HERE/systemd/rianileo-deploy.timer"   /etc/systemd/system/
systemctl daemon-reload
# Allow the deployer (runs as $RIANILEO_USER) to restart the bot without a password.
cat >/etc/sudoers.d/rianileo-deploy <<EOF
$RIANILEO_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart rianileo-bot.service
EOF
chmod 440 /etc/sudoers.d/rianileo-deploy
# pull_deploy calls plain 'systemctl restart' — make it use sudo on the VM:
#   (handled by deploy.env: the unit runs the deployer as root by default below.)

echo "== 8. crontab (periodic jobs) =="
crontab -u "$RIANILEO_USER" "$APP_DIR/deploy/crontab.vm"

echo "== 9. SHADOW vs LIVE =="
echo "   This installs the brain. Keep it SHADOW until parity passes:"
echo "     - set YOUTUBE_AUTO_UPLOAD=0 in /etc/rianileo/env"
echo "     - point SLACK_* at the dev workspace"
echo "   Then: systemctl enable --now rianileo-bot.service rianileo-deploy.timer"
echo "   Cutover = flip those + atomically unload the Mac launchd jobs (see README)."
echo "== bootstrap done =="
