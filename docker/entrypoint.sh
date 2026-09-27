#!/bin/sh
set -eu

if [ ! -S /var/run/docker.sock ]; then
    echo 'Docker socket /var/run/docker.sock is unavailable.' >&2
    exit 1
fi

mkdir -p /data
chown -R app:app /data

socket_gid=$(stat -c '%g' /var/run/docker.sock)
socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true)
if [ -z "$socket_group" ]; then
    groupadd --gid "$socket_gid" dockerhost
    socket_group=dockerhost
fi
usermod -aG "$socket_group" app

gosu app python manage.py migrate --noinput
exec gosu app gunicorn container_core.wsgi:application \
    --bind 0.0.0.0:8000 --workers 2 --timeout 900 --access-logfile - --error-logfile -
