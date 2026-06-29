#!/usr/bin/env bash
#
# deploy.sh — pull the latest code for THIS VM's branch and apply it.
#
# The VM is a one-way mirror of GitHub: whatever branch is checked out here
# (prod on PRD, preprod on PPRD) is force-synced to its remote counterpart.
# Local edits on the server are intentionally discarded.
#
# Usage (over SSH, as OSPUser):
#     cd /home/OSPUser/EcostruxureLight
#     ./deploy/deploy.sh
#
set -euo pipefail

APP_DIR="/home/OSPUser/EcostruxureLight"
SERVICE="ecostruxure-light"
export DJANGO_SETTINGS_MODULE="config.settings.production"

cd "$APP_DIR"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo ">> Deploying $APP_DIR  (branch: $BRANCH)"

# 1) Match the remote branch exactly (discard any local drift).
git fetch --prune origin
git reset --hard "origin/${BRANCH}"

# 2) Dependencies (cheap no-op when nothing changed).
source venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 3) Database schema + static assets (production settings).
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# 4) Restart the app server and show its state.
sudo systemctl restart "$SERVICE"
sleep 1
sudo systemctl --no-pager --lines=0 status "$SERVICE" | head -n 5

echo ">> Deploy complete: $(git rev-parse --short HEAD) on ${BRANCH}"
