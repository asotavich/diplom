"""
analyzer/models.py

Django data models for the Frontend Architecture Analyzer application.

Models:
    - Project:       Groups analysis scans per user (FR-02).
    - AnalysisReport: Stores the full result of a single analysis run,
                      including raw component counts, configurable weight
                      coefficients W_i, and the computed Architectural
                      Complexity Index C (FR-04, FR-10, Section 1.3.4).

Mathematical model (Section 1.3.4)
-----------------------------------
Given the web-page tuple  WP = (L, S, Sc), the Complexity Index is:

    C = Σ W_i * N_i   for i in {links, styles, scripts}

Subject to the normalisation constraint:

    W_links + W_styles + W_scripts = 1.0

Default weights are equal (1/3 each) and can be overridden per report
to satisfy FR-04 (user-adjustable coefficients).
"""

import re
import secrets
from decimal import Decimal
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()

# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

#: Characters not allowed in a slug — collapsed to a single dash.
_SLUG_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")
#: Length of the random hex suffix appended to the deterministic base.
_SLUG_RANDOM_HEX_BYTES = 3  # → 6 hex characters, e.g. "a8f3b6"
#: Hard cap on the deterministic base so the final slug fits SlugField max_length.
_SLUG_BASE_MAX_LENGTH = 80


def _slugify_base(value: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to dashes, trim ends."""
    return _SLUG_NON_ALPHANUM.sub("-", (value or "").lower()).strip("-")


def build_report_slug_base(report: "AnalysisReport") -> str:
    """
    Deterministic part of a report slug — derived from the scan target.

    URL source: hostname with dots → dashes ("www.google.com" → "google-com").
    FILE source: uploaded file's basename without extension, sanitised.
    Falls back to ``"report"`` if neither yields a usable string.
    """
    if report.source_type == AnalysisReport.SourceType.FILE and report.uploaded_file:
        raw = report.uploaded_file.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    else:
        host = (urlparse(report.url or "").hostname or "")
        if host.lower().startswith("www."):
            host = host[4:]
        raw = host

    base = _slugify_base(raw)
    if not base:
        return "report"
    return base[:_SLUG_BASE_MAX_LENGTH].rstrip("-") or "report"


def generate_unique_report_slug(report: "AnalysisReport") -> str:
    """
    Build a unique slug for ``report``: ``<base>-<6 hex chars>``.

    Re-rolls the random suffix on the unlikely event of a collision so the
    DB-level uniqueness constraint never trips for a legitimate save.
    """
    base = build_report_slug_base(report)
    qs = AnalysisReport.objects.exclude(pk=report.pk) if report.pk else AnalysisReport.objects.all()
    for _attempt in range(8):
        candidate = f"{base}-{secrets.token_hex(_SLUG_RANDOM_HEX_BYTES)}"
        if not qs.filter(slug=candidate).exists():
            return candidate
    raise RuntimeError(  # pragma: no cover — astronomically unlikely
        f"Could not allocate a unique slug for base '{base}' after 8 attempts."
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default equal-weight distribution across the three component groups.
DEFAULT_WEIGHT = Decimal("0.3333")
#: Tolerance used when verifying the normalisation constraint Σ W_i = 1.
WEIGHT_SUM_TOLERANCE = Decimal("0.01")


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_weights_sum_to_one(report: "AnalysisReport") -> None:
    """
    Enforce the normalisation constraint from Section 1.3.4:

        W_links + W_styles + W_scripts = 1  (within WEIGHT_SUM_TOLERANCE)

    Called from AnalysisReport.clean() so it runs on both form saves and
    explicit model validation, but not on every database write (for
    performance-critical bulk inserts the caller is responsible).
    """
    total = report.weight_links + report.weight_styles + report.weight_scripts
    if abs(total - Decimal("1.0")) > WEIGHT_SUM_TOLERANCE:
        raise ValidationError(
            _(
                "Weight coefficients must sum to 1.0 "
                "(got %(total)s). Adjust W_links, W_styles, or W_scripts."
            ),
            params={"total": total},
        )


# ---------------------------------------------------------------------------
# Project  (FR-02)
# ---------------------------------------------------------------------------

class Project(models.Model):
    """
    An *Audit Project* groups one or more analysis scans so that complexity
    trends can be tracked over time (FR-02, FR-10).

    Ownership is tied to the standard Django User model (FR-01).
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects",
        verbose_name=_("owner"),
        help_text=_("The user who created and owns this project."),
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("project name"),
        help_text=_("A human-readable label for the audit project."),
    )
    description = models.TextField(
        blank=True,
        verbose_name=_("description"),
        help_text=_("Optional notes or context about this project."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        verbose_name = _("project")
        verbose_name_plural = _("projects")
        ordering = ["-created_at"]
        # A single user cannot have two projects with the same name.
        unique_together = [("owner", "name")]

    def __str__(self) -> str:
        return f"{self.name} (owner: {self.owner})"


# ---------------------------------------------------------------------------
# AnalysisReport  (FR-04, FR-10, Section 1.3.4)
# ---------------------------------------------------------------------------

class AnalysisReport(models.Model):
    """
    Persists the full result of one analysis scan.

    Component counts
    ----------------
    The web page is decomposed into the mathematical tuple  WP = (L, S, Sc):

    * ``count_links``   – cardinality |L|  (internal navigation links)
    * ``count_styles``  – cardinality |S|  (external CSS stylesheets)
    * ``count_scripts`` – cardinality |Sc| (client-side JS scripts)

    Weight coefficients
    -------------------
    ``weight_links``, ``weight_styles``, ``weight_scripts`` correspond to
    W_1, W_2, W_3 from Section 1.3.4.  They must satisfy Σ W_i = 1.

    Complexity index
    ----------------
    ``complexity_index`` stores the pre-computed value of C so that the
    database can be queried / sorted without recalculation at runtime.
    Call :meth:`compute_complexity` to obtain C and :meth:`save_with_complexity`
    to persist it atomically.

    Lifecycle
    ---------
    Created in ``PENDING`` by the API; advanced to ``RUNNING`` / ``SUCCESS``
    / ``FAILED`` by the Celery task ``analyzer.tasks.run_analysis``.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        RUNNING = "RUNNING", _("Running")
        SUCCESS = "SUCCESS", _("Success")
        FAILED = "FAILED", _("Failed")

    class SourceType(models.TextChoices):
        URL = "URL", _("Public URL")
        FILE = "FILE", _("Uploaded HTML file")

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="reports",
        null=True,
        blank=True,
        verbose_name=_("project"),
        help_text=_("Optional audit project this scan belongs to."),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="analysis_reports",
        verbose_name=_("created by"),
        help_text=_("The user who triggered this analysis run (FR-01)."),
    )

    # ------------------------------------------------------------------
    # Input metadata
    # ------------------------------------------------------------------

    source_type = models.CharField(
        max_length=4,
        choices=SourceType.choices,
        default=SourceType.URL,
        verbose_name=_("source type"),
        help_text=_(
            "Where the analysed HTML came from. URL = remote fetch by Celery, "
            "FILE = direct HTML upload from the browser (FR-03)."
        ),
    )
    url = models.URLField(
        max_length=2048,
        validators=[URLValidator()],
        blank=True,
        null=True,
        verbose_name=_("analysed URL"),
        help_text=_(
            "URL of the page analysed (FR-03). Required when source_type = URL; "
            "left empty for FILE uploads."
        ),
    )
    uploaded_file = models.FileField(
        upload_to="uploads/%Y/%m/",
        blank=True,
        null=True,
        max_length=512,
        verbose_name=_("uploaded HTML file"),
        help_text=_(
            "Direct HTML upload (FR-03). Used by the Celery worker when "
            "source_type = FILE; ignored otherwise."
        ),
    )
    scanned_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("scanned at"),
        help_text=_("Timestamp of when the analysis was performed."),
    )
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        verbose_name=_("slug"),
        help_text=_(
            "URL-safe public identifier (e.g. ``google-com-a8f3b6``). Auto-generated "
            "on creation; combines the target's hostname/filename with a random "
            "suffix so primary-key values are never exposed to the client."
        ),
    )

    # ------------------------------------------------------------------
    # Raw component counts  — N_i in the formula
    # ------------------------------------------------------------------

    count_links = models.PositiveIntegerField(
        default=0,
        verbose_name=_("link count (L)"),
        help_text=_("Number of internal navigation links extracted from the page (|L|)."),
    )
    count_styles = models.PositiveIntegerField(
        default=0,
        verbose_name=_("style count (S)"),
        help_text=_("Number of external CSS stylesheets found on the page (|S|)."),
    )
    count_scripts = models.PositiveIntegerField(
        default=0,
        verbose_name=_("script count (Sc)"),
        help_text=_("Number of client-side JS scripts referenced by the page (|Sc|)."),
    )

    # ------------------------------------------------------------------
    # Weight coefficients  — W_i in the formula  (FR-04)
    # ------------------------------------------------------------------

    _weight_validator = [
        MinValueValidator(Decimal("0.0")),
        MaxValueValidator(Decimal("1.0")),
    ]

    weight_links = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=DEFAULT_WEIGHT,
        validators=_weight_validator,
        verbose_name=_("weight W_links"),
        help_text=_(
            "Relative importance of navigation links in the complexity formula. "
            "Must satisfy W_links + W_styles + W_scripts = 1."
        ),
    )
    weight_styles = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=DEFAULT_WEIGHT,
        validators=_weight_validator,
        verbose_name=_("weight W_styles"),
        help_text=_(
            "Relative importance of stylesheets in the complexity formula. "
            "Must satisfy W_links + W_styles + W_scripts = 1."
        ),
    )
    weight_scripts = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=DEFAULT_WEIGHT,
        validators=_weight_validator,
        verbose_name=_("weight W_scripts"),
        help_text=_(
            "Relative importance of scripts in the complexity formula. "
            "Must satisfy W_links + W_styles + W_scripts = 1."
        ),
    )

    # ------------------------------------------------------------------
    # Derived / computed fields
    # ------------------------------------------------------------------

    complexity_index = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name=_("complexity index (C)"),
        help_text=_(
            "Architectural Complexity Index C = Σ W_i * N_i as defined in "
            "Section 1.3.4. Populated automatically by save_with_complexity()."
        ),
    )

    # Optional: store serialised raw WP tuple for audit / debugging.
    raw_metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("raw metadata"),
        help_text=_(
            "Serialised WP tuple {links: [...], styles: [...], scripts: [...]} "
            "for audit trail and future re-analysis."
        ),
    )

    # ------------------------------------------------------------------
    # Async task tracking
    # ------------------------------------------------------------------

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("status"),
        help_text=_("Lifecycle of the background scan task."),
    )
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name=_("celery task id"),
        help_text=_("ID returned by Celery when the scan was dispatched."),
    )
    error_message = models.TextField(
        blank=True,
        verbose_name=_("error message"),
        help_text=_("Populated when status is FAILED."),
    )

    class Meta:
        verbose_name = _("analysis report")
        verbose_name_plural = _("analysis reports")
        ordering = ["-scanned_at"]
        indexes = [
            # Speed up history queries for a given project (FR-10).
            models.Index(fields=["project", "-scanned_at"], name="idx_report_project_time"),
            # Speed up the "my reports" list on the user dashboard.
            models.Index(fields=["created_by", "-scanned_at"], name="idx_report_user_time"),
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self) -> None:
        """
        Run model-level validation.

        Enforces the normalisation constraint Σ W_i = 1 (Section 1.3.4)
        so that Django admin, DRF serialisers, and ModelForms all benefit.
        """
        super().clean()
        _validate_weights_sum_to_one(self)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, *args, **kwargs) -> None:
        """
        Auto-populate :attr:`slug` on first save so callers don't have to
        think about it. Existing slugs are never overwritten — once the
        client has a URL pointing at a report, that URL stays stable.
        """
        if not self.slug:
            self.slug = generate_unique_report_slug(self)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    def compute_complexity(self) -> Decimal:
        """
        Calculate and return the Architectural Complexity Index.

        Formula (Section 1.3.4)::

            C = W_links * N_links + W_styles * N_styles + W_scripts * N_scripts

        Returns
        -------
        Decimal
            The computed value of C, rounded to 4 decimal places.

        Notes
        -----
        This method does **not** persist the result; use
        :meth:`save_with_complexity` to calculate *and* save atomically.
        """
        c = (
            self.weight_links * Decimal(self.count_links)
            + self.weight_styles * Decimal(self.count_styles)
            + self.weight_scripts * Decimal(self.count_scripts)
        )
        return c.quantize(Decimal("0.0001"))

    def save_with_complexity(self, **kwargs) -> None:
        """
        Compute C, store it in :attr:`complexity_index`, then save.

        Usage::

            report = AnalysisReport(project=project, url="https://example.com", ...)
            report.save_with_complexity()

        Parameters
        ----------
        **kwargs
            Forwarded verbatim to :meth:`django.db.models.Model.save`.
        """
        self.complexity_index = self.compute_complexity()
        self.full_clean()  # Runs clean() → weight normalisation check.
        self.save(**kwargs)

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return (
            f"Report [{self.pk}] — {self.url} "
            f"(C={self.complexity_index}, scanned: {self.scanned_at})"
        )
