"""
Django settings for the FEAnalyzer project (production-ready).

Configuration is driven by environment variables (see `.env.production`
or the per-service `environment:` blocks in docker-compose.yml). Locally
you can drop a `.env` file next to `manage.py` and it will be read
automatically.
"""

import sys
from datetime import timedelta
from pathlib import Path

import environ

#: True when this process is running under ``manage.py test``. Used to
#: substitute a zero-configuration in-memory SQLite database so the test
#: suite stays runnable in CI without a Postgres service container.
TESTING = "test" in sys.argv

# ---------------------------------------------------------------------------
# Paths & environment loading
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    JWT_ACCESS_TOKEN_LIFETIME_MIN=(int, 15),
    JWT_REFRESH_TOKEN_LIFETIME_DAYS=(int, 7),
)

# Local-dev convenience: pull variables from a .env file if it exists.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(str(_env_file))


# ---------------------------------------------------------------------------
# Core security settings
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG")

# ``HTTPS_ENFORCED`` gates every setting that requires the request chain to
# be terminating TLS upstream (HSTS, Secure cookies, SSL redirect). It is
# independent of ``DEBUG`` so a "docker compose up" run on plain HTTP
# localhost can opt out without flipping DEBUG on. Defaults to ``not
# DEBUG`` so production stays hardened. For HTTP-only local testing, set
# ``DJANGO_HTTPS_ENFORCED=0``.
HTTPS_ENFORCED = env.bool("DJANGO_HTTPS_ENFORCED", default=not DEBUG)

# Dedicated signing key for SimpleJWT (fixing audit finding C-A). Decoupling
# this from DJANGO_SECRET_KEY means a SECRET_KEY rotation no longer
# invalidates every issued JWT, and a SECRET_KEY leak does not let an
# attacker forge tokens. Falls back to SECRET_KEY only if the dedicated key
# is not configured, so existing dev deployments keep working.
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY", default=SECRET_KEY)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "drf_spectacular_sidecar",  # bundles Swagger UI & ReDoc static assets
    # Local
    "analyzer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CORS must come before CommonMiddleware (django-cors-headers docs).
    "corsheaders.middleware.CorsMiddleware",
    # WhiteNoise sits right after SecurityMiddleware for static serving.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "frontend_analyzer.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "frontend_analyzer.wsgi.application"
ASGI_APPLICATION = "frontend_analyzer.asgi.application"


# ---------------------------------------------------------------------------
# Database (PostgreSQL via psycopg 3)
# ---------------------------------------------------------------------------
if TESTING:
    # CI / local test runs: in-memory SQLite avoids needing a Postgres
    # service. The schema is recreated from migrations on every run.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER"),
            "PASSWORD": env("POSTGRES_PASSWORD"),
            "HOST": env("POSTGRES_HOST", default="db"),
            "PORT": env.int("POSTGRES_PORT", default=5432),
            "CONN_MAX_AGE": 60,
            "ATOMIC_REQUESTS": False,
        }
    }


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        # Audit finding M-8: 8 chars is below 2026 NIST SP 800-63B guidance.
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

# WhiteNoise compressed + manifest storage for production asset caching.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Django REST Framework + SimpleJWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",
        "user": "300/minute",
        # Per-user cap on POST /api/reports/. Protects the Celery worker
        # pool from a single user queuing hundreds of scans.
        "analysis_create": "10/minute",
        # Audit M-7 — independent budget for /api/auth/verify/ so token
        # probing cannot exhaust the SPA's general anon allowance.
        "token_verify": "30/minute",
    },
    # drf-spectacular: use its AutoSchema for OpenAPI 3 generation.
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_TOKEN_LIFETIME_MIN")),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# Audit C-B — refresh tokens live in an httpOnly Secure SameSite cookie
# issued by /api/auth/login|register|refresh/, never in JS-reachable
# storage. Access tokens stay short-lived (15 min default) and are kept in
# memory only on the client.
#
# ``JWT_REFRESH_COOKIE_SECURE`` follows ``HTTPS_ENFORCED`` so a browser
# loading the SPA over HTTP localhost still receives & stores the cookie
# during dev. In production (``HTTPS_ENFORCED=True``) the Secure attribute
# is set, so the cookie cannot accidentally leak over plain HTTP.
#
# ``JWT_REFRESH_COOKIE_SAMESITE`` defaults to ``Lax``: it still protects
# the refresh endpoint from cross-site POST CSRF (browsers strip the
# cookie from cross-site fetch/XHR requests), but does not break the
# top-level navigation flows that ``Strict`` would interrupt. Override
# to ``Strict`` in production via the env var if your deployment never
# needs cross-site link-throughs.
JWT_REFRESH_COOKIE_NAME = "feanalyzer_refresh"
JWT_REFRESH_COOKIE_PATH = "/api/auth/"
JWT_REFRESH_COOKIE_SAMESITE = env("JWT_REFRESH_COOKIE_SAMESITE", default="Lax")
JWT_REFRESH_COOKIE_SECURE = HTTPS_ENFORCED


# ---------------------------------------------------------------------------
# CORS (React SPA origin allow-list)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True


# ---------------------------------------------------------------------------
# Celery (wiring completed in Stage 3; placed here so env vars are picked up
# from a single source of truth).
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/1")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 90
CELERY_RESULT_EXPIRES = 60 * 60 * 24  # keep task results for 24h


# ---------------------------------------------------------------------------
# Security hardening — see ``HTTPS_ENFORCED`` defined at the top of this
# file. Settings below are split into "always safe under HTTP" and
# "requires TLS in front" so an HTTP-localhost dev run does not get
# 301-redirected to a port that has no TLS listener.
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# These are safe regardless of HTTPS — they don't break HTTP traffic.
if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    X_FRAME_OPTIONS = "DENY"

# These ONLY make sense when TLS is actually present in front of the app.
if HTTPS_ENFORCED:
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Audit finding M-1: ensure plain-HTTP requests get a 301 to HTTPS so
    # HSTS can reach the browser on the very first visit.
    SECURE_SSL_REDIRECT = True


# ---------------------------------------------------------------------------
# Logging — route everything to stdout so Docker captures it.
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL"),
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "analyzer": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


# ---------------------------------------------------------------------------
# drf-spectacular — OpenAPI 3 schema + Swagger UI
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "FEAnalyzer API",
    "DESCRIPTION": (
        "REST API for the **Frontend Architecture Complexity Analyzer**.\n\n"
        "## Authentication\n"
        "All endpoints (except `/api/auth/register/` and `/api/auth/login/`) "
        "require a JWT access token.\n\n"
        "**How to authenticate in Swagger UI:**\n"
        "1. Call `POST /api/auth/login/` with your credentials.\n"
        "2. Copy the `access` token from the response.\n"
        "3. Click the **Authorize** button at the top of this page.\n"
        "4. Paste the token as `Bearer <your_token>` and click **Authorize**.\n\n"
        "## Complexity Index\n"
        "The Architectural Complexity Index is computed as:\n\n"
        "    C = W_links × N_links + W_styles × N_styles + W_scripts × N_scripts\n\n"
        "where `W_links + W_styles + W_scripts = 1.0` (configurable per scan)."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,   # hide the raw schema URL from the schema itself
    "SCHEMA_PATH_PREFIX": "/api/",
    "COMPONENT_SPLIT_REQUEST": True,  # separate request vs response schemas
    "SORT_OPERATIONS": False,         # preserve declaration order in the UI
    # Use bundled (sidecar) assets so the UI works without a CDN.
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}
