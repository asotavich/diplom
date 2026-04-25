"""
Stage 7 migration — public slug identifier for AnalysisReport.

Replaces numeric primary keys in URLs (e.g. ``/reports/15/``) with stable,
human-readable, hard-to-enumerate slugs like ``google-com-a8f3b6``.

The migration runs in three steps so it works against tables with existing
rows:

1. Add ``slug`` as a nullable, non-unique column.
2. Backfill every existing row with a freshly generated slug.
3. Tighten the column to ``NOT NULL`` + ``UNIQUE``.

The backfill logic is intentionally inlined (rather than imported from
``analyzer.models``) so future model changes can't retroactively break this
historical migration.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import urlparse

from django.db import migrations, models

_SLUG_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")
_SLUG_BASE_MAX_LENGTH = 80


def _slug_base(report) -> str:
    if report.source_type == "FILE" and report.uploaded_file:
        raw = report.uploaded_file.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    else:
        host = (urlparse(report.url or "").hostname or "")
        if host.lower().startswith("www."):
            host = host[4:]
        raw = host

    base = _SLUG_NON_ALPHANUM.sub("-", (raw or "").lower()).strip("-")
    base = base[:_SLUG_BASE_MAX_LENGTH].rstrip("-")
    return base or "report"


def _populate_slugs(apps, schema_editor):
    AnalysisReport = apps.get_model("analyzer", "AnalysisReport")
    used = set(
        AnalysisReport.objects.exclude(slug="").values_list("slug", flat=True)
    )
    for report in AnalysisReport.objects.filter(slug=""):
        base = _slug_base(report)
        for _attempt in range(8):
            candidate = f"{base}-{secrets.token_hex(3)}"
            if candidate not in used:
                used.add(candidate)
                report.slug = candidate
                report.save(update_fields=["slug"])
                break
        else:  # pragma: no cover
            raise RuntimeError(
                f"Could not allocate a unique slug for report pk={report.pk}"
            )


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0004_add_file_upload"),
    ]

    # NOTE: the AddField intentionally sets ``db_index=False`` even though
    # SlugField defaults to indexed. Otherwise PostgreSQL creates the
    # ``<table>_<col>_<hash>_like`` companion index for varchar_pattern_ops
    # eagerly, and the subsequent ``AlterField`` to ``unique=True`` then
    # tries to create a *fresh* ``_like`` index that collides with the
    # existing one ("relation ... already exists"). Skipping the initial
    # index lets the final AlterField add both the unique constraint and
    # its ``_like`` companion in one clean step.
    operations = [
        migrations.AddField(
            model_name="analysisreport",
            name="slug",
            field=models.SlugField(
                blank=True,
                db_index=False,
                default="",
                max_length=140,
                help_text=(
                    "URL-safe public identifier (e.g. ``google-com-a8f3b6``). "
                    "Auto-generated on creation; combines the target's hostname/"
                    "filename with a random suffix so primary-key values are "
                    "never exposed to the client."
                ),
                verbose_name="slug",
            ),
            preserve_default=False,
        ),
        migrations.RunPython(_populate_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="analysisreport",
            name="slug",
            field=models.SlugField(
                blank=True,
                max_length=140,
                unique=True,
                help_text=(
                    "URL-safe public identifier (e.g. ``google-com-a8f3b6``). "
                    "Auto-generated on creation; combines the target's hostname/"
                    "filename with a random suffix so primary-key values are "
                    "never exposed to the client."
                ),
                verbose_name="slug",
            ),
        ),
    ]
