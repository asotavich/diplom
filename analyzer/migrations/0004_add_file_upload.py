"""
Stage 6 migration — direct HTML file upload (FR-03).

Adds two fields and relaxes one:

* ``source_type``  — discriminator: URL vs FILE.
* ``uploaded_file`` — FileField written when the user uploads HTML directly.
* ``url``          — now nullable / blank-allowed so FILE-source rows are valid.
"""

from django.core.validators import URLValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0003_add_task_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisreport",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("URL", "Public URL"),
                    ("FILE", "Uploaded HTML file"),
                ],
                default="URL",
                help_text=(
                    "Where the analysed HTML came from. URL = remote fetch by "
                    "Celery, FILE = direct HTML upload from the browser (FR-03)."
                ),
                max_length=4,
                verbose_name="source type",
            ),
        ),
        migrations.AddField(
            model_name="analysisreport",
            name="uploaded_file",
            field=models.FileField(
                blank=True,
                null=True,
                max_length=512,
                upload_to="uploads/%Y/%m/",
                help_text=(
                    "Direct HTML upload (FR-03). Used by the Celery worker when "
                    "source_type = FILE; ignored otherwise."
                ),
                verbose_name="uploaded HTML file",
            ),
        ),
        migrations.AlterField(
            model_name="analysisreport",
            name="url",
            field=models.URLField(
                blank=True,
                null=True,
                max_length=2048,
                validators=[URLValidator()],
                help_text=(
                    "URL of the page analysed (FR-03). Required when "
                    "source_type = URL; left empty for FILE uploads."
                ),
                verbose_name="analysed URL",
            ),
        ),
    ]
