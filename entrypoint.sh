#!/bin/sh
# =============================================================================
# FEAnalyzer — container entrypoint
# Responsibilities:
#   1. If started as root, fix ownership of the mounted static/media volumes
#      (Docker mounts named volumes as root:root regardless of image perms),
#      then re-exec this script as the unprivileged `django` user via gosu.
#   2. Block until PostgreSQL and Redis are reachable (both are optional; if
#      the matching *_HOST env var is empty we skip the wait).
#   3. Optionally apply migrations and collect static files (controlled by
#      DJANGO_MIGRATE and DJANGO_COLLECTSTATIC so the celery container can
#      share the image without stepping on the web container's work).
#   4. exec the command passed by CMD / compose (gunicorn, celery worker, ...).
# =============================================================================
set -e

# Step 1: permission fix-up, then drop privileges.
if [ "$(id -u)" = "0" ]; then
    echo "[entrypoint] Ensuring django owns /app/staticfiles and /app/mediafiles..."
    chown -R django:django /app/staticfiles /app/mediafiles
    echo "[entrypoint] Dropping privileges to django user..."
    exec gosu django "$0" "$@"
fi

wait_for() {
    WAIT_HOST="$1"
    WAIT_PORT="$2"
    WAIT_LABEL="$3"
    echo "[entrypoint] Waiting for ${WAIT_LABEL} at ${WAIT_HOST}:${WAIT_PORT}..."
    export WAIT_HOST WAIT_PORT WAIT_LABEL
    python - <<'PY'
import os, socket, sys, time

host = os.environ["WAIT_HOST"]
port = int(os.environ["WAIT_PORT"])
label = os.environ.get("WAIT_LABEL", host)

deadline = time.time() + 60
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except OSError:
        time.sleep(1)

print(f"[entrypoint] Timed out waiting for {label} at {host}:{port}", file=sys.stderr)
sys.exit(1)
PY
    echo "[entrypoint] ${WAIT_LABEL} is reachable."
}

if [ -n "${POSTGRES_HOST}" ]; then
    wait_for "${POSTGRES_HOST}" "${POSTGRES_PORT:-5432}" "PostgreSQL"
fi

if [ -n "${REDIS_HOST}" ]; then
    wait_for "${REDIS_HOST}" "${REDIS_PORT:-6379}" "Redis"
fi

if [ "${DJANGO_MIGRATE:-0}" = "1" ]; then
    echo "[entrypoint] Applying database migrations..."
    python manage.py migrate --noinput
fi

if [ "${DJANGO_COLLECTSTATIC:-0}" = "1" ]; then
    echo "[entrypoint] Collecting static files..."
    python manage.py collectstatic --noinput
fi

echo "[entrypoint] Starting: $*"
exec "$@"
