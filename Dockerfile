# =============================================================================
# FEAnalyzer — Production Docker image
# Multi-stage build:
#   1. `builder` compiles wheels (so build tools do not ship to production).
#   2. `runtime` installs those wheels on a slim image and runs Gunicorn as a
#      non-root user.
# The same image is reused by the `web` and `celery` services in
# docker-compose.yml; the command line is overridden per service.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — builder
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System packages required to compile native wheels (psycopg[binary] ships
# prebuilt, but lxml and a few others benefit from a real toolchain).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt


# -----------------------------------------------------------------------------
# Stage 2 — runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=frontend_analyzer.settings \
    PATH="/home/django/.local/bin:${PATH}"

# Runtime-only system libs: libpq5 for psycopg, libxml2/libxslt for lxml,
# curl for the Dockerfile-level healthcheck, gosu for dropping privileges
# from the entrypoint after mounted-volume ownership has been fixed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libxml2 \
        libxslt1.1 \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 1001 django \
    && adduser --system --uid 1001 --gid 1001 --home /home/django django

WORKDIR /app

# Install the pre-built wheels from the builder stage.
COPY --from=builder /wheels /wheels
COPY --from=builder /build/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Copy project source with the correct ownership.
COPY --chown=django:django . /app

# Static / media collection points (will be shared with nginx via Docker volumes).
RUN mkdir -p /app/staticfiles /app/mediafiles \
    && chmod +x /app/entrypoint.sh \
    && chown -R django:django /app

# NOTE: we intentionally do NOT `USER django` here. The entrypoint boots as
# root so it can chown the named static/media volumes (which Docker mounts
# owned by root:root regardless of the image's directory ownership), then
# drops to the `django` user via gosu before exec'ing the real command.

EXPOSE 8000

# Gunicorn bound to all interfaces; logs to stdout/stderr so `docker logs`
# captures everything. Workers and timeout tuned for a small VPS; override
# via compose environment if needed.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "frontend_analyzer.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1
