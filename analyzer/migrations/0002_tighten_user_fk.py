"""
Stage 2 migration — tightens the user linkage on AnalysisReport:

* ``project`` becomes nullable so users can file ad-hoc scans that
  aren't yet organised into an audit project.
* ``created_by`` becomes non-nullable with ``on_delete=CASCADE`` so a
  report is now strictly owned by exactly one user; deleting the user
  removes their reports.
* Adds an index on (created_by, -scanned_at) to keep the per-user
  dashboard query cheap as report history grows.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("analyzer", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="analysisreport",
            name="project",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional audit project this scan belongs to.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reports",
                to="analyzer.project",
                verbose_name="project",
            ),
        ),
        migrations.AlterField(
            model_name="analysisreport",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user who triggered this analysis run (FR-01).",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="analysis_reports",
                to=settings.AUTH_USER_MODEL,
                verbose_name="created by",
            ),
        ),
        migrations.AddIndex(
            model_name="analysisreport",
            index=models.Index(
                fields=["created_by", "-scanned_at"],
                name="idx_report_user_time",
            ),
        ),
    ]
