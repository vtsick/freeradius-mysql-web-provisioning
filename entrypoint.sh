#!/bin/sh
set -e

rm -f /etc/nginx/sites-enabled/default

nginx

exec gunicorn "$@"
