#!/bin/sh
# Run one scheduled Flask command with the production environment, never two copies at once.
# Usage (from cron): /srv/gg_flask/deploy/run-job.sh studio sync-sales
# Output goes to syslog/journal under the tag "gg-flask-job". Run it as the ggflask user
# (cron does; by hand: sudo -u ggflask deploy/run-job.sh ...) so lock files stay shared.
set -eu

APP_DIR=/srv/gg_flask
ENV_FILE=/etc/gg-flask/env
LOCK_NAME=$(printf '%s' "$*" | tr -c 'A-Za-z0-9' '-')

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

cd "$APP_DIR"
# -n: if the previous run is still going, skip this one instead of piling up.
/usr/bin/flock -n "/run/lock/gg-flask-$LOCK_NAME.lock" \
    "$APP_DIR/.venv/bin/flask" --app wsgi "$@" 2>&1 | /usr/bin/logger -t gg-flask-job
