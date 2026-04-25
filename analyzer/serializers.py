"""
analyzer/serializers.py

Data-transfer objects for the REST API. Four serializers live here:

* ``UserRegistrationSerializer`` — POST /api/auth/register/
* ``UserProfileSerializer``      — GET/PATCH /api/auth/profile/
* ``ProjectSerializer``          — /api/projects/
* ``AnalysisReportSerializer``   — /api/reports/

All per-user scoping happens in the view layer so these serializers stay
purely about field validation and representation.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (
    DEFAULT_WEIGHT,
    WEIGHT_SUM_TOLERANCE,
    AnalysisReport,
    Project,
)
from .plantuml import build_plantuml

User = get_user_model()


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Accepts a username/email/password (with confirmation) and creates a
    Django user. Password complexity is enforced by Django's configured
    validators (see ``AUTH_PASSWORD_VALIDATORS`` in settings).
    """

    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
    )
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password", "password_confirm")

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def validate(self, attrs: dict) -> dict:
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs

    def create(self, validated_data: dict) -> User:
        validated_data.pop("password_confirm")
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
        )


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Read-mostly representation of the logged-in user's profile. ``username``
    is treated as immutable — if that ever needs to change we'll add a
    dedicated endpoint rather than silently overloading this one.
    """

    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name", "date_joined")
        read_only_fields = ("id", "username", "date_joined")


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class ProjectSerializer(serializers.ModelSerializer):
    report_count = serializers.IntegerField(source="reports.count", read_only=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "description",
            "created_at",
            "updated_at",
            "report_count",
        )
        read_only_fields = ("id", "created_at", "updated_at", "report_count")


# ---------------------------------------------------------------------------
# Analysis report
# ---------------------------------------------------------------------------

class AnalysisReportSerializer(serializers.ModelSerializer):
    """
    Mirrors the full AnalysisReport row. Raw counts and the complexity
    index are read-only — they're populated server-side from the scraping
    task (Stage 3). The client supplies URL **or** an uploaded HTML file
    (FR-03), weights, and an optional project assignment.
    """

    project_name = serializers.CharField(
        source="project.name",
        read_only=True,
        default=None,
    )
    uploaded_file = serializers.FileField(
        write_only=True,
        required=False,
        allow_null=True,
        help_text=(
            "Direct HTML upload (FR-03). When supplied, ``url`` may be omitted "
            "and ``source_type`` is implicitly set to FILE."
        ),
    )
    uploaded_file_name = serializers.SerializerMethodField()
    plantuml_source = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisReport
        fields = (
            "id",
            "slug",
            "project",
            "project_name",
            "source_type",
            "url",
            "uploaded_file",
            "uploaded_file_name",
            "scanned_at",
            "count_links",
            "count_styles",
            "count_scripts",
            "weight_links",
            "weight_styles",
            "weight_scripts",
            "complexity_index",
            "raw_metadata",
            "plantuml_source",
            "status",
            "celery_task_id",
            "error_message",
        )
        read_only_fields = (
            "id",
            "slug",
            "project_name",
            "source_type",
            "uploaded_file_name",
            "scanned_at",
            "count_links",
            "count_styles",
            "count_scripts",
            "complexity_index",
            "raw_metadata",
            "plantuml_source",
            "status",
            "celery_task_id",
            "error_message",
        )

    # --- Computed fields --------------------------------------------------
    def get_uploaded_file_name(self, obj: AnalysisReport) -> str | None:
        if not obj.uploaded_file:
            return None
        return obj.uploaded_file.name.rsplit("/", 1)[-1]

    def get_plantuml_source(self, obj: AnalysisReport) -> str | None:
        # Only meaningful for completed scans — pending/failed rows have no
        # raw_metadata to draw, and emitting a placeholder diagram would be
        # misleading.
        if obj.status != AnalysisReport.Status.SUCCESS:
            return None
        return build_plantuml(obj)

    # --- Weight-sum normalisation (Section 1.3.4) -------------------------
    def validate(self, attrs: dict) -> dict:
        weight_links = attrs.get("weight_links", DEFAULT_WEIGHT)
        weight_styles = attrs.get("weight_styles", DEFAULT_WEIGHT)
        weight_scripts = attrs.get("weight_scripts", DEFAULT_WEIGHT)

        total = Decimal(weight_links) + Decimal(weight_styles) + Decimal(weight_scripts)
        if abs(total - Decimal("1.0")) > WEIGHT_SUM_TOLERANCE:
            raise serializers.ValidationError(
                {
                    "weights": (
                        f"Weight coefficients must sum to 1.0 (got {total}). "
                        "Adjust weight_links, weight_styles, or weight_scripts."
                    )
                }
            )

        # FR-03 — exactly one of {url, uploaded_file} must be supplied on create.
        # Detect this by checking whether either is present in attrs (we are in
        # create mode when there is no instance yet; for updates the row already
        # has its source baked in and we don't validate it again).
        if self.instance is None:
            url = (attrs.get("url") or "").strip() if attrs.get("url") else ""
            uploaded = attrs.get("uploaded_file")
            if url and uploaded:
                raise serializers.ValidationError(
                    {"detail": "Provide either a URL or an HTML file, not both."}
                )
            if not url and not uploaded:
                raise serializers.ValidationError(
                    {"detail": "Provide either a URL or an HTML file."}
                )
            if uploaded:
                # Cheap sanity check on the upload — full HTML validation
                # happens in the Celery worker.
                name = (uploaded.name or "").lower()
                if not name.endswith((".html", ".htm")):
                    raise serializers.ValidationError(
                        {"uploaded_file": "File must be an .html or .htm document."}
                    )
                if uploaded.size > 5 * 1024 * 1024:
                    raise serializers.ValidationError(
                        {"uploaded_file": "File too large (max 5 MB)."}
                    )

        return attrs

    # --- Project-ownership check -----------------------------------------
    def validate_project(self, project: Project | None) -> Project | None:
        """
        Reject attempts to file reports into a project owned by a different
        user. The ``request.user`` is available via the DRF context.
        """
        if project is None:
            return None
        request = self.context.get("request")
        if request and project.owner_id != request.user.id:
            raise serializers.ValidationError(
                "You can only file reports into your own projects."
            )
        return project


# ---------------------------------------------------------------------------
# Task status (polling endpoint)
# ---------------------------------------------------------------------------

class TaskStatusSerializer(serializers.Serializer):
    """
    Read-only shape returned by ``GET /api/tasks/<task_id>/``. Keeps the
    response minimal so the frontend's 2-second polling loop stays cheap.
    """

    task_id = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=AnalysisReport.Status.choices,
        read_only=True,
    )
    report_id = serializers.IntegerField(read_only=True, allow_null=True)
    report_slug = serializers.CharField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_blank=True)
    complexity_index = serializers.DecimalField(
        max_digits=12,
        decimal_places=4,
        read_only=True,
        allow_null=True,
    )
