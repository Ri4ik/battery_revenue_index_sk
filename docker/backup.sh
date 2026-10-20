#!/bin/sh
set -eu
umask 077
file=$(mktemp "/backups/manual-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.dump")
trap 'rm -f "$file"' 0
pg_dump --format=custom --file="$file"
trap - 0
printf 'Backup saved: %s\n' "$file"
