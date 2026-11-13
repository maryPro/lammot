#!/bin/sh
# Starts LammotBot. Railway mounts volumes owned by root, so when the container
# starts as root we hand the database folder to the app user and drop root
# before the app starts. The app itself never runs as root.
set -eu

DB_DIR="$(dirname "${DATABASE_PATH:-/data/lammot.db}")"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DB_DIR"
    chown -R 10001:10001 "$DB_DIR"
    exec setpriv --reuid=10001 --regid=10001 --clear-groups "$0" "$@"
fi

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips="*" \
    --no-server-header
