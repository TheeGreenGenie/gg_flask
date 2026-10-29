#!/bin/sh
# Run any Flask command on SiteGround with the app's settings, e.g.
#   sh ~/.ggflask/current/deploy/siteground/flask.sh admin create --email you@example.com --role owner
#   sh ~/.ggflask/current/deploy/siteground/flask.sh studio sync-users
#   sh ~/.ggflask/current/deploy/siteground/flask.sh mail test you@example.com
set -eu
GG_HOME="${GG_HOME:-$HOME/.ggflask}"
set -a
# shellcheck disable=SC1091
. "$GG_HOME/env"
set +a
export MEDIA_ROOT="${MEDIA_ROOT:-$GG_HOME/media}"
export JOBS_HEARTBEAT_DIR="${JOBS_HEARTBEAT_DIR:-$GG_HOME/jobs}"
cd "$GG_HOME/current"
exec "$GG_HOME/venv/bin/flask" --app wsgi "$@"
