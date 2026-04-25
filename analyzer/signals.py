"""
analyzer/signals.py

Side effects that should fire automatically on AnalysisReport lifecycle
events. Registered from :class:`AnalyzerConfig.ready` so the receivers
are connected exactly once at app start-up.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import AnalysisReport

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=AnalysisReport)
def cleanup_uploaded_file(sender, instance: AnalysisReport, **kwargs) -> None:
    """
    Remove the on-disk HTML file backing a deleted AnalysisReport.

    Django's ``FileField`` does not cascade-delete the underlying file
    when the row is removed, which leaves orphaned blobs in
    ``mediafiles/uploads/...`` over time (FR-03 hardening). We wire this
    on ``post_delete`` rather than ``pre_delete`` so the file is only
    removed *after* the row deletion has actually committed — if the row
    delete were rolled back we would otherwise have lost the file but
    kept a dangling reference.

    A missing file is treated as success: the on-disk state we wanted
    has already been reached.
    """
    file_field = instance.uploaded_file
    if not file_field:
        return
    name = file_field.name
    try:
        file_field.delete(save=False)
    except FileNotFoundError:
        logger.debug("cleanup_uploaded_file: %s already absent", name)
    except Exception:  # noqa: BLE001 — defensive, never block the delete
        logger.exception("cleanup_uploaded_file: failed to remove %s", name)
