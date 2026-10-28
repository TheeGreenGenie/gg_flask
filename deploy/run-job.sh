#!/bin/sh
# Run one scheduled Flask command with the production environment, never two copies at once.
# Usage (from cron): /srv/gg_flask/deploy/run-job.sh studio sync-sales
# Output goes to syslog/journal under the tag "gg-flask-job". Run it as the ggflask user
# (cron does; by hand: sudo -u ggflask deploy/run-job.sh ...) so lock files stay shared.
#
# Every run ends with one line in the log and a heartbeat file in instance/jobs/ saying ok,
# failed or skipped (the previous run was still going), with the exit code and duration.
# `flask --app wsgi jobs check` reads those files and reports jobs that failed or stopped
# running; the owner's daily summary email includes the same warning.
set -u

APP_DIR=/srv/gg_flask
ENV_FILE=/etc/gg-flask/env
JOB_NAME=$(printf '%s' "$*" | tr -c 'A-Za-z0-9' '-')
HEARTBEAT_DIR="$APP_DIR/instance/jobs"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

cd "$APP_DIR" || exit 1
mkdir -p "$HEARTBEAT_DIR"
OUTPUT=$(mktemp)
trap 'rm -f "$OUTPUT"' EXIT

STARTED=$(date +%s)
# -n: if the previous run is still going, skip this one instead of piling up; -E 75 marks that
# case with its own exit code so a skip is never confused with a failure.
/usr/bin/flock -n -E 75 "/run/lock/gg-flask-$JOB_NAME.lock" \
    "$APP_DIR/.venv/bin/flask" --app wsgi "$@" >"$OUTPUT" 2>&1
CODE=$?
SECONDS_TAKEN=$(( $(date +%s) - STARTED ))

case "$CODE" in
    0) RESULT=ok ;;
    75) RESULT=skipped ;;
    *) RESULT=failed ;;
esac

if [ -s "$OUTPUT" ]; then
    /usr/bin/logger -t gg-flask-job < "$OUTPUT"
fi
/usr/bin/logger -t gg-flask-job "job=$JOB_NAME result=$RESULT exit=$CODE seconds=$SECONDS_TAKEN"
printf '{"job": "%s", "result": "%s", "exit": %s, "finished": %s, "seconds": %s}\n' \
    "$JOB_NAME" "$RESULT" "$CODE" "$(date +%s)" "$SECONDS_TAKEN" > "$HEARTBEAT_DIR/$JOB_NAME.json.tmp" \
    && mv "$HEARTBEAT_DIR/$JOB_NAME.json.tmp" "$HEARTBEAT_DIR/$JOB_NAME.json"

# A skip is expected now and then; only a real failure makes cron see an error.
[ "$RESULT" = failed ] && exit "$CODE"
exit 0
