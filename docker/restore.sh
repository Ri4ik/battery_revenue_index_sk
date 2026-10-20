#!/bin/sh
set -eu
if [ "$#" -ne 1 ] || [ "${RESTORE_CONFIRM:-}" != "replace-database" ]; then
    echo 'Usage: RESTORE_CONFIRM=replace-database sh docker/restore.sh /backups/FILE.dump' >&2
    exit 1
fi
case "$1" in
    /backups/*.dump) ;;
    *) echo 'Expected an existing /backups/*.dump file' >&2; exit 1 ;;
esac
test -f "$1"
# Stop application writers before using this command. Transactional restore avoids partial state.
pg_restore --exit-on-error --single-transaction --clean --if-exists --no-owner --no-acl \
    --dbname="$PGDATABASE" "$1"
