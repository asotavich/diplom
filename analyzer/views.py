"""
analyzer/views.py

REST API for the FEAnalyzer service. All endpoints require a valid JWT
access token except for registration and the SimpleJWT token obtain /
refresh views (wired directly in ``analyzer/urls.py``).

Endpoint map
------------
POST   /api/auth/register/          — create a user + return access/refresh
GET    /api/auth/profile/           — current user's profile
PATCH  /api/auth/profile/           — update profile (email/first/last name)
GET    /api/projects/               — list current user's projects
POST   /api/projects/               — create a project for current user
GET    /api/projects/<id>/          — retrieve a project (owner-scoped)
PATCH  /api/projects/<id>/          — update a project
DELETE /api/projects/<id>/          — delete a project
GET    /api/reports/                — list current user's reports
POST   /api/reports/                — submit URL for async analysis (202)
GET    /api/reports/<id>/           — retrieve a report (owner-scoped)
DELETE /api/reports/<id>/           — delete a report
GET    /api/reports/<id>/export/    — download report as Excel workbook
GET    /api/reports/<id>/export.pdf/ — download report as PDF document (FR-09)
GET    /api/tasks/<task_id>/        — poll background-scan status
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from urllib.parse import urlparse

from celery.utils import uuid as celery_uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import generics, permissions, serializers as drf_serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from .exports import build_report_excel, build_report_pdf
from .models import AnalysisReport, Project
from .serializers import (
    AnalysisReportSerializer,
    ProjectSerializer,
    TaskStatusSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
)
from .tasks import run_analysis

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# Refresh-cookie helpers (audit C-B)
# ---------------------------------------------------------------------------
#
# The refresh token never appears in a JSON response body or in
# JavaScript-reachable storage. It is set as an httpOnly Secure
# SameSite=Strict cookie scoped to ``/api/auth/`` so only the auth
# endpoints below can ever see it; the access token stays short-lived
# and lives in the React process memory only.
# ---------------------------------------------------------------------------

def _set_refresh_cookie(response: Response, refresh_value: str) -> None:
    """Attach the refresh JWT to ``response`` as a hardened cookie."""
    response.set_cookie(
        key=settings.JWT_REFRESH_COOKIE_NAME,
        value=refresh_value,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.JWT_REFRESH_COOKIE_SECURE,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
        path=settings.JWT_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.JWT_REFRESH_COOKIE_NAME,
        path=settings.JWT_REFRESH_COOKIE_PATH,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
    )


def _inject_refresh_from_cookie(request) -> None:
    """
    SimpleJWT's serializers expect ``refresh`` in the request body. Lift it
    out of the cookie so the existing serializer pipeline works unchanged.
    """
    cookie = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)
    if not cookie:
        return
    try:
        data = request.data.copy()
    except AttributeError:
        data = dict(request.data)
    data["refresh"] = cookie
    # DRF caches parsed data on the request; replace it.
    request._full_data = data


class CookieTokenObtainPairView(TokenObtainPairView):
    """
    POST /api/auth/login/ — returns the access token in JSON, sets the
    refresh token as an httpOnly cookie. Audit C-B.
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            refresh = response.data.pop("refresh", None)
            if refresh:
                _set_refresh_cookie(response, refresh)
        return response


class CookieTokenRefreshView(TokenRefreshView):
    """
    POST /api/auth/refresh/ — reads the refresh token from the cookie,
    returns a fresh access token in JSON, and rotates the cookie value.
    Audit C-B.
    """

    def post(self, request, *args, **kwargs):
        _inject_refresh_from_cookie(request)
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            new_refresh = response.data.pop("refresh", None)
            if new_refresh:
                _set_refresh_cookie(response, new_refresh)
        return response


class CookieTokenBlacklistView(TokenBlacklistView):
    """
    POST /api/auth/logout/ — pulls the refresh token from the cookie,
    blacklists it, then clears the cookie. Audit C-B.
    """

    def post(self, request, *args, **kwargs):
        _inject_refresh_from_cookie(request)
        try:
            response = super().post(request, *args, **kwargs)
        except Exception:
            # Even if blacklisting fails (e.g. token already expired) we
            # still want to clear the cookie client-side.
            response = Response(status=status.HTTP_205_RESET_CONTENT)
        _clear_refresh_cookie(response)
        return response


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Register a new user account",
    description=(
        "Creates a new user, returns the short-lived access token in the "
        "JSON body and the long-lived refresh token in an httpOnly Secure "
        "SameSite=Strict cookie so it is never reachable from JavaScript "
        "(audit C-B)."
    ),
    responses={
        201: inline_serializer(
            name="RegisterResponse",
            fields={
                "user": UserProfileSerializer(),
                "access": drf_serializers.CharField(
                    help_text="Short-lived JWT access token (default 15 min)."
                ),
            },
        ),
        400: OpenApiResponse(
            description=(
                "Validation error: duplicate username/email, weak password, "
                "or password confirmation mismatch."
            )
        ),
    },
)
class RegisterView(generics.CreateAPIView):
    """
    POST /api/auth/register/

    Creates a new user and immediately issues a JWT access/refresh pair so
    the client can skip the extra /login/ round-trip after signup. The
    refresh half is delivered as an httpOnly cookie (audit C-B).
    """

    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    queryset = User.objects.none()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        logger.info("New user registered: '%s' (id=%s)", user.username, user.pk)

        response = Response(
            {
                "user": UserProfileSerializer(user).data,
                "access": str(refresh.access_token),
            },
            status=status.HTTP_201_CREATED,
        )
        _set_refresh_cookie(response, str(refresh))
        return response


class UserProfileView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/auth/profile/ — always scoped to the caller."""

    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self) -> User:
        return self.request.user


# ---------------------------------------------------------------------------
# Projects (owner-scoped)
# ---------------------------------------------------------------------------

class ProjectListCreateView(generics.ListCreateAPIView):
    serializer_class = ProjectSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ProjectDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProjectSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)


# ---------------------------------------------------------------------------
# Reports (owner-scoped; create dispatches a Celery task)
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        summary="List your analysis reports",
        description=(
            "Returns a paginated list of all analysis reports created by the "
            "current user, ordered newest-first. Use `?page=N` for pagination."
        ),
    ),
    create=extend_schema(
        summary="Submit a URL for analysis",
        description=(
            "Enqueues an asynchronous Celery task that fetches the target URL, "
            "counts its links / stylesheets / scripts, and computes the "
            "Complexity Index C = Σ W_i × N_i.\n\n"
            "Returns **202 Accepted** immediately with a `task_id` — poll "
            "`GET /api/tasks/{task_id}/` every few seconds until `status` "
            "is `SUCCESS` or `FAILED`."
        ),
        request=AnalysisReportSerializer,
        responses={
            202: inline_serializer(
                name="AnalysisSubmitResponse",
                fields={
                    "task_id": drf_serializers.UUIDField(
                        help_text="Celery task UUID. Pass to the polling endpoint."
                    ),
                    "report_id": drf_serializers.IntegerField(
                        help_text="DB primary key of the created report."
                    ),
                    "status": drf_serializers.CharField(
                        help_text="Always `PENDING` immediately after submission."
                    ),
                    "status_url": drf_serializers.URLField(
                        help_text="Absolute URL to poll for task completion."
                    ),
                    "report_url": drf_serializers.URLField(
                        help_text="Absolute URL of the full report (readable once SUCCESS)."
                    ),
                },
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error: invalid URL, weight coefficients not "
                    "summing to 1.0, or project not owned by the current user."
                )
            ),
        },
    ),
)
class AnalysisReportListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/reports/  — paginated history, newest first.
    POST /api/reports/  — 202 Accepted; dispatches a Celery scan.

    The row is written to the DB *before* the task fires so the dashboard
    can display in-flight scans. We reserve the Celery task ID up front
    (``celery.utils.uuid``) and pass it explicitly to ``apply_async`` so
    the DB row and the task ID agree atomically.
    """

    serializer_class = AnalysisReportSerializer
    permission_classes = [permissions.IsAuthenticated]
    # FR-03: also accept multipart/form-data so the React app can post a
    # raw HTML upload alongside its weight fields. The default parsers stay
    # available for plain JSON URL submissions.
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        return (
            AnalysisReport.objects
            .filter(created_by=self.request.user)
            .select_related("project")
            .order_by("-scanned_at")
        )

    def get_throttles(self):
        """
        Apply a tighter per-user cap on POST (scan submission), since each
        scan reserves a Celery worker. GETs keep the global UserRateThrottle.
        """
        if self.request.method == "POST":
            self.throttle_scope = "analysis_create"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        task_id = celery_uuid()
        validated = serializer.validated_data

        uploaded_file = validated.get("uploaded_file")
        is_file_source = bool(uploaded_file)

        report = AnalysisReport.objects.create(
            created_by=request.user,
            project=validated.get("project"),
            source_type=(
                AnalysisReport.SourceType.FILE if is_file_source
                else AnalysisReport.SourceType.URL
            ),
            url=None if is_file_source else validated.get("url"),
            uploaded_file=uploaded_file if is_file_source else None,
            weight_links=validated.get(
                "weight_links",
                AnalysisReport._meta.get_field("weight_links").default,
            ),
            weight_styles=validated.get(
                "weight_styles",
                AnalysisReport._meta.get_field("weight_styles").default,
            ),
            weight_scripts=validated.get(
                "weight_scripts",
                AnalysisReport._meta.get_field("weight_scripts").default,
            ),
            status=AnalysisReport.Status.PENDING,
            celery_task_id=task_id,
        )

        run_analysis.apply_async(args=[report.pk], task_id=task_id)
        logger.info(
            "Dispatched run_analysis task=%s report=%s source=%s user=%s",
            task_id, report.pk, report.source_type, request.user.pk,
        )

        return Response(
            {
                "task_id": task_id,
                "report_id": report.pk,
                "status": report.status,
                "status_url": reverse(
                    "analyzer:task_status",
                    kwargs={"task_id": task_id},
                    request=request,
                ),
                "report_url": reverse(
                    "analyzer:report_detail",
                    kwargs={"slug": report.slug},
                    request=request,
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class AnalysisReportDetailView(generics.RetrieveDestroyAPIView):
    """GET/DELETE /api/reports/<slug>/ — owner-scoped, 404 otherwise."""

    serializer_class = AnalysisReportSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "slug"

    def get_queryset(self):
        return (
            AnalysisReport.objects
            .filter(created_by=self.request.user)
            .select_related("project")
        )


# ---------------------------------------------------------------------------
# Report export
# ---------------------------------------------------------------------------

_SAFE_HOST_CHARS = re.compile(r"[^a-z0-9]+")


def _host_slug(url: str) -> str:
    """
    Extract the hostname from ``url`` and return a filesystem-safe slug.

    Examples
    --------
    >>> _host_slug("https://www.Google.com/search?q=x")
    'google-com'
    >>> _host_slug("http://sub.example.co.uk:8080/")
    'sub-example-co-uk'
    >>> _host_slug("not a url")
    'report'
    """
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    slug = _SAFE_HOST_CHARS.sub("-", host).strip("-")
    return slug or "report"


def _report_filename_stem(report) -> str:
    """
    Pick a filesystem-safe filename stem for an exported report.

    URL-source reports use the host slug (``google-com``); FILE-source
    reports use the original upload's basename without its extension. In
    both cases the result is sanitised to ``[a-z0-9-]+``.
    """
    if report.source_type == "FILE" and report.uploaded_file:
        base = report.uploaded_file.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        slug = _SAFE_HOST_CHARS.sub("-", base.lower()).strip("-")
        return slug or "uploaded"
    return _host_slug(report.url or "")


class ReportExportView(APIView):
    """
    GET /api/reports/<pk>/export/

    Generates and streams a formatted Excel (.xlsx) workbook for the given
    completed report. The workbook contains two sheets:

    * **Summary** — report metadata, component counts (L / S / Sc),
      weight coefficients, complexity index with colour-coded level.
    * **Resource Breakdown** — internal vs. external split per component
      type and a ranked table of the top external hosts.

    Returns 409 Conflict if the report has not yet reached SUCCESS status.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Export report as Excel workbook",
        description=(
            "Generates a downloadable `.xlsx` file containing the full analysis "
            "report: metadata, component counts, weight coefficients, complexity "
            "index (colour-coded by level), and external host breakdown.\n\n"
            "Only available for reports in **SUCCESS** status — returns **409** "
            "if the scan is still PENDING / RUNNING or has FAILED."
        ),
        responses={
            (200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): (
                OpenApiTypes.BINARY
            ),
            404: OpenApiResponse(description="Report not found or not owned by current user."),
            409: OpenApiResponse(description="Report is not in SUCCESS status yet."),
        },
    )
    def get(self, request, slug: str):
        report = get_object_or_404(
            AnalysisReport.objects.select_related("project", "created_by"),
            slug=slug,
            created_by=request.user,
        )

        if report.status != AnalysisReport.Status.SUCCESS:
            return Response(
                {"detail": f"Report is not ready for export (status: {report.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        wb = build_report_excel(report)
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.read(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        filename = f"feanalyzer-report-{_report_filename_stem(report)}.xlsx"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        logger.info(
            "Report %s (slug=%s) exported as Excel by user %s",
            report.pk, slug, request.user.pk,
        )
        return response


class ReportExportPdfView(APIView):
    """
    GET /api/reports/<pk>/export.pdf/

    Renders the report as a single-file PDF document (FR-09). Same data
    as the Excel summary plus a ranked table of the top external hosts.
    Returns 409 Conflict if the report has not yet reached SUCCESS status.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Export report as PDF document",
        description=(
            "Generates a downloadable `.pdf` file containing the full analysis "
            "report — metadata, component counts, weight coefficients, complexity "
            "index (colour-coded by level), and external host breakdown.\n\n"
            "Only available for reports in **SUCCESS** status — returns **409** "
            "if the scan is still PENDING / RUNNING or has FAILED."
        ),
        responses={
            (200, "application/pdf"): OpenApiTypes.BINARY,
            404: OpenApiResponse(description="Report not found or not owned by current user."),
            409: OpenApiResponse(description="Report is not in SUCCESS status yet."),
        },
    )
    def get(self, request, slug: str):
        report = get_object_or_404(
            AnalysisReport.objects.select_related("project", "created_by"),
            slug=slug,
            created_by=request.user,
        )

        if report.status != AnalysisReport.Status.SUCCESS:
            return Response(
                {"detail": f"Report is not ready for export (status: {report.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        pdf_bytes = build_report_pdf(report)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        filename = f"feanalyzer-report-{_report_filename_stem(report)}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        logger.info(
            "Report %s (slug=%s) exported as PDF by user %s",
            report.pk, slug, request.user.pk,
        )
        return response


# ---------------------------------------------------------------------------
# Task polling
# ---------------------------------------------------------------------------

class TaskStatusView(APIView):
    """
    GET /api/tasks/<task_id>/

    Lightweight endpoint hit by the React poll loop. The AnalysisReport
    row is the source of truth — Celery's own result backend is treated
    as advisory. Returns 404 if the task ID is unknown to this user,
    which doubles as an access-control check (a user can't poll another
    user's scan).
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TaskStatusSerializer

    @extend_schema(
        summary="Poll background analysis task",
        description=(
            "Lightweight polling endpoint consumed by the frontend every 2 seconds. "
            "Returns the current `status` of the scan. Also acts as an "
            "access-control gate: returns **404** if `task_id` does not belong "
            "to the currently authenticated user."
        ),
        responses={
            200: TaskStatusSerializer,
            404: OpenApiResponse(
                description="Task not found or not owned by the current user."
            ),
        },
    )
    def get(self, request, task_id: str):
        try:
            report = AnalysisReport.objects.only(
                "id", "slug", "status", "error_message", "complexity_index",
                "created_by_id",
            ).get(celery_task_id=task_id, created_by=request.user)
        except AnalysisReport.DoesNotExist:
            return Response(
                {"detail": "Task not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_done = report.status == AnalysisReport.Status.SUCCESS
        payload = {
            "task_id": task_id,
            "status": report.status,
            "report_id": report.pk if is_done else None,
            "report_slug": report.slug if is_done else None,
            "error_message": report.error_message,
            "complexity_index": report.complexity_index,
        }
        return Response(TaskStatusSerializer(payload).data)
