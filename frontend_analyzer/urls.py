"""
Root URL configuration.

Top-level mount points:

* /admin/       — Django admin (staff only)
* /healthz/     — liveness probe (no DB query, no auth)
* /api/         — REST API (analyzer app)
* /api/schema/  — OpenAPI 3 schema (raw JSON/YAML, drf-spectacular)
* /api/docs/    — Swagger UI  (interactive API browser)
* /api/redoc/   — ReDoc UI    (alternative documentation view)
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def healthz(_request):
    """Return 200 OK with a tiny JSON payload. Runs no DB queries."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("api/", include("analyzer.urls", namespace="analyzer")),

    # ---- OpenAPI 3 schema + UI ------------------------------------------
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

# In DEBUG mode, let Django serve /media/ directly so developers don't need
# Nginx running locally. In production Nginx handles /media/ from a shared
# volume (see nginx/nginx.conf).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
