#!/bin/sh
set -e

rm -f /etc/nginx/sites-enabled/default

exec python /app/container_start.py "$@"
