#!/bin/sh
# Run one scheduled Flask command on SiteGround, never two copies at once.
# Usage (Site Tools > Devs > Cron Jobs):  sh ~/.ggflask/current/deploy/siteground/run-job.sh studio sync-sales
#
# The Node wrapper links ~/.ggflask/current to the deployed code and builds ~/.ggflask/venv, so
# this works whether or not the site is awake. Output goes to ~/.ggflask/logs/jobs.log, and each
# run leaves a heartbeat in ~/.ggflask/jobs/ (ok, failed or skipped) that `jobs check` and the
# owner's daily summary read.
set -u

GG_HOME="${GG_HOME:-$HOME/.ggflask}"
APP_DIR="$GG_HOME/current"
JOB_NAME=$(printf '%s' "$*" | tr -c 'A-Za-z0-9' '-')
HEARTBEAT_DIR="$GG_HOME/jobs"
LOG="$GG_HOME/logs/jobs.log"
LOCK_DIR="$GG_HOME/run/job-$JOB_NAME.lock"

set -a
# shellcheck disable=SC1091
. "$GG_HOME/env"
set +a
export MEDIA_ROOT="${MEDIA_ROOT:-$GG_HOME/media}"
export JOBS_HEARTBEAT_DIR="$HEARTBEAT_DIR"
export GG_PLATFORM=siteground

cd "$APP_DIR" || exit 1
mkdir -p "$HEARTBEAT_DIR" "$GG_HOME/logs"

STARTED=$(date +%s)
# mkdir is atomic: if the previous run still holds the lock, skip instead of piling up.
# A lock older than an hour is left over from a crash and is cleared.
if [ -d "$LOCK_DIR" ] && [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
    rmdir "$LOCK_DIR" 2>/dev/null
fi
if mkdir "$LOCK_DIR" 2>/dev/null; then
    trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT
    OUTPUT=$("$GG_HOME/venv/bin/flask" --app wsgi "$@" 2>&1)
    CODE=$?
else
    OUTPUT=""
    CODE=75
fi
SECONDS_TAKEN=$(( $(date +%s) - STARTED ))

case "$CODE" in
    0) RESULT=ok ;;
    75) RESULT=skipped ;;
    *) RESULT=failed ;;
esac

{
    [ -n "$OUTPUT" ] && printf '%s\n' "$OUTPUT"
    printf '%s job=%s result=%s exit=%s seconds=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$JOB_NAME" "$RESULT" "$CODE" "$SECONDS_TAKEN"
} >> "$LOG"
printf '{"job": "%s", "result": "%s", "exit": %s, "finished": %s, "seconds": %s}\n' \
    "$JOB_NAME" "$RESULT" "$CODE" "$(date +%s)" "$SECONDS_TAKEN" > "$HEARTBEAT_DIR/$JOB_NAME.json.tmp" \
    && mv "$HEARTBEAT_DIR/$JOB_NAME.json.tmp" "$HEARTBEAT_DIR/$JOB_NAME.json"

[ "$RESULT" = failed ] && exit "$CODE"
exit 0
