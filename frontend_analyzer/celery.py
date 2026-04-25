"""
Celery application for the FEAnalyzer project.

Conventions:
* All Celery config keys live in Django settings under the ``CELERY_``
  prefix and are picked up via ``namespace="CELERY"``.
* Task modules are auto-discovered — any ``tasks.py`` inside an installed
  app is registered automatically (see the ``analyzer/tasks.py`` module).

Worker startup::

    celery -A frontend_analyzer worker --loglevel=info
"""

from __future__ import annotations

import os

from celery import Celery

# Make Django settings importable both from the worker and from any
# shell spawned by ``celery`` sub-commands.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "frontend_analyzer.settings")

app = Celery("frontend_analyzer")

# Read all CELERY_* keys from Django settings. This keeps a single
# source of truth (env-driven via django-environ) rather than a parallel
# celeryconfig.py.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-load tasks from every installed Django app.
app.autodiscover_tasks()


@app.task(bind=True, name="frontend_analyzer.debug_task")
def debug_task(self) -> str:
    """Trivial sanity task: ``celery -A frontend_analyzer call frontend_analyzer.debug_task``."""
    return f"debug_task ok (request_id={self.request.id})"
