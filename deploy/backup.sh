#!/bin/sh
# Nightly backup: PostgreSQL dump + media folder, kept 14 days locally.
# Copy BACKUP_DIR off the server too (another machine or cloud storage) — a backup on the same
# disk does not survive a disk failure. Restore steps: DEPLOY.md "Restore".
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/gg-flask}
MEDIA_DIR=${MEDIA_DIR:-/srv/gg_flask/instance/media}
DB_NAME=${DB_NAME:-gg_site}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

umask 077
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/media"

# Custom-format dump: compressed, restorable with pg_restore (whole DB or single tables).
sudo -u postgres pg_dump --format=custom --no-owner "$DB_NAME" > "$BACKUP_DIR/db/$DB_NAME-$STAMP.dump"

# Media: hard-linked daily snapshots (unchanged files take no extra space).
LATEST="$BACKUP_DIR/media/latest"
if [ -d "$LATEST" ]; then
    rsync -a --delete --link-dest="$LATEST" "$MEDIA_DIR/" "$BACKUP_DIR/media/$STAMP/"
else
    rsync -a --delete "$MEDIA_DIR/" "$BACKUP_DIR/media/$STAMP/"
fi
ln -sfn "$BACKUP_DIR/media/$STAMP" "$LATEST"

find "$BACKUP_DIR/db" -name '*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR/media" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

logger -t gg-flask-backup "backup $STAMP done"

# Heartbeat for `flask jobs check` / the daily summary (a failed run never reaches this line,
# so a missing or old heartbeat means the backup is not happening).
HEARTBEAT_DIR=${HEARTBEAT_DIR:-/srv/gg_flask/instance/jobs}
if [ -d "$HEARTBEAT_DIR" ]; then
    (umask 022; printf '{"job": "backup", "result": "ok", "exit": 0, "finished": %s, "seconds": 0}\n' \
        "$(date +%s)" > "$HEARTBEAT_DIR/backup.json")
fi
