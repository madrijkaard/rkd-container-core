#!/bin/sh
set -eu

case "${DJANGO_SECRET_KEY:-}" in
    *[![:space:]]*) ;;
    *)
        echo 'Erro: DJANGO_SECRET_KEY não está configurada. Preencha o .env ou execute configure-secret-key.sh antes de iniciar o backend.' >&2
        exit 1
        ;;
esac

if [ ! -S /var/run/docker.sock ]; then
    echo 'Docker socket /var/run/docker.sock is unavailable.' >&2
    exit 1
fi

mkdir -p /data
chown -R app:app /data

# Preserve SQLite data from deployments created before the project rename.
if [ -n "${DJANGO_DB_PATH:-}" ] && [ ! -e "$DJANGO_DB_PATH" ]; then
    case "$DJANGO_DB_PATH" in
        */rkd_dockestra_core.sqlite3) legacy_db="${DJANGO_DB_PATH%/*}/container_core.sqlite3" ;;
        */rkd_dockestra_core.local.sqlite3) legacy_db="${DJANGO_DB_PATH%/*}/container_core.local.sqlite3" ;;
        *) legacy_db= ;;
    esac
    if [ -n "$legacy_db" ] && [ -f "$legacy_db" ]; then
        gosu app python - "$legacy_db" "$DJANGO_DB_PATH" <<'PY'
import sqlite3
import sys
import os
import tempfile

directory = os.path.dirname(sys.argv[2])
descriptor, temporary_path = tempfile.mkstemp(prefix='.dockestra-db-', suffix='.sqlite3', dir=directory)
os.close(descriptor)
try:
    with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(temporary_path) as target:
        source.backup(target)
    os.replace(temporary_path, sys.argv[2])
except BaseException:
    os.unlink(temporary_path)
    raise
PY
    fi
fi

socket_gid=$(stat -c '%g' /var/run/docker.sock)
socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true)
if [ -z "$socket_group" ]; then
    groupadd --gid "$socket_gid" dockerhost
    socket_group=dockerhost
fi
usermod -aG "$socket_group" app

gosu app python manage.py migrate --noinput
exec gosu app gunicorn rkd_dockestra_core.wsgi:application \
    --bind 0.0.0.0:8000 --workers 2 --timeout 900 --access-logfile - --error-logfile -
