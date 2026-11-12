#!/bin/sh
# Start im Container (P8): cron im Hintergrund, danach gunicorn als Nutzer ohne Root.
# cron selbst braucht root; die Jobs in /etc/cron.d/rvkompass laufen als Nutzer rvkompass.
set -eu

cron

exec setpriv --reuid=rvkompass --regid=rvkompass --init-groups \
    gunicorn -c /app/docker/gunicorn.conf.py "app:create_app()"
