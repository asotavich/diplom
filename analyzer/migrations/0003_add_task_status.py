"""
Stage 3 migration — async task tracking.

Adds three fields to ``AnalysisReport`` so the Celery-driven scan flow
can record state transitions and post-mortem errors:

* ``status``          — lifecycle (PENDING / RUNNING / SUCCESS / FAILED)
* ``celery_task_id``  — ID returned by Celery on dispatch (indexed so
                        the status-polling endpoint is a single-row lookup)
* ``error_message``   — human-readable failure reason
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0002_tighten_user_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisreport",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("RUNNING", "Running"),
                    ("SUCCESS", "Success"),
                    ("FAILED", "Failed"),
                ],
                default="PENDING",
                help_text="Lifecycle of the background scan task.",
                max_length=10,
                verbose_name="status",
            ),
        ),
        migrations.AddField(
            model_name="analysisreport",
            name="celery_task_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="ID returned by Celery when the scan was dispatched.",
                max_length=255,
                verbose_name="celery task id",
            ),
        ),
        migrations.AddField(
            model_name="analysisreport",
            name="error_message",
            field=models.TextField(
                blank=True,
                help_text="Populated when status is FAILED.",
                verbose_name="error message",
            ),
        ),
    ]
