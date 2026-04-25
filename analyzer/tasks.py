"""
analyzer/tasks.py

Background scan task. The API creates the ``AnalysisReport`` row in
``PENDING`` state, then dispatches this task with the report's primary
key — the task is responsible for transitioning the row through
``RUNNING`` → (``SUCCESS`` | ``FAILED``) and populating the count /
complexity / metadata fields.

Retry policy
------------
* Network / transient errors (``requests.RequestException``) trigger up
  to ``MAX_RETRIES`` retries with an exponentially increasing backoff.
* Any other exception is treated as terminal: the row is stamped
  ``FAILED`` with the exception text and the task returns without
  re-raising (so Celery's own retry machinery doesn't kick in a second
  time).
"""

from __future__ import annotations

import logging
import os

import requests
from celery import shared_task

from .models import AnalysisReport
from .services import UnsafeURLError, analyze_html_content, analyze_webpage

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 10


@shared_task(
    bind=True,
    name="analyzer.run_analysis",
    max_retries=MAX_RETRIES,
    acks_late=True,
    track_started=True,
)
def run_analysis(self, report_id: int) -> dict:
    """
    Fetch the target URL of the given report, update its fields, and
    mark the report ``SUCCESS``.

    Parameters
    ----------
    report_id : int
        Primary key of a pre-created ``AnalysisReport`` in ``PENDING``.

    Returns
    -------
    dict
        ``{"report_id": <int>, "status": "SUCCESS" | "FAILED"}`` — the
        shape the frontend polling endpoint mirrors back to the client.
    """
    logger.info("run_analysis: starting task=%s report=%s", self.request.id, report_id)

    try:
        report = AnalysisReport.objects.get(pk=report_id)
    except AnalysisReport.DoesNotExist:
        # Nothing to retry against — the row was deleted mid-flight.
        logger.error("run_analysis: report %s no longer exists; abandoning.", report_id)
        return {"report_id": report_id, "status": "MISSING"}

    # Advance to RUNNING so the dashboard shows in-flight scans.
    AnalysisReport.objects.filter(pk=report_id).update(
        status=AnalysisReport.Status.RUNNING,
    )

    try:
        if report.source_type == AnalysisReport.SourceType.FILE:
            if not report.uploaded_file:
                return _mark_failed(report_id, "Uploaded file is missing.")
            with report.uploaded_file.open("rb") as fh:
                html_bytes = fh.read()
            label = os.path.basename(report.uploaded_file.name) or "uploaded.html"
            analysis = analyze_html_content(html_bytes, source_label=label)
        else:
            if not report.url:
                return _mark_failed(report_id, "Report has no URL to analyse.")
            analysis = analyze_webpage(report.url)

    except UnsafeURLError as exc:
        # SSRF guard rejected the URL or one of its redirects. This is a
        # permanent / user-correctable error — do NOT retry.
        logger.warning("run_analysis: unsafe URL for report=%s err=%s", report_id, exc)
        return _mark_failed(report_id, f"Unsafe URL: {exc}")

    except requests.RequestException as exc:
        # Transient network issues — retry a couple of times before giving up.
        if self.request.retries < MAX_RETRIES:
            logger.warning(
                "run_analysis: retryable failure for report=%s attempt=%s err=%s",
                report_id, self.request.retries + 1, exc,
            )
            raise self.retry(exc=exc, countdown=RETRY_BACKOFF_SEC * (self.request.retries + 1))
        return _mark_failed(report_id, f"Network error: {exc}")

    except Exception as exc:  # noqa: BLE001 — terminal, record and exit
        logger.exception("run_analysis: permanent failure for report=%s", report_id)
        return _mark_failed(report_id, str(exc))

    # Success path — refresh the instance so we pick up the RUNNING status
    # row we just wrote, then persist the computed values.
    report.refresh_from_db()
    report.count_links = analysis["count_links"]
    report.count_styles = analysis["count_styles"]
    report.count_scripts = analysis["count_scripts"]
    report.raw_metadata = analysis["raw_metadata"]
    report.status = AnalysisReport.Status.SUCCESS
    report.error_message = ""
    report.save_with_complexity()

    logger.info(
        "run_analysis: report=%s complete C=%s (L=%s S=%s Sc=%s)",
        report_id,
        report.complexity_index,
        report.count_links,
        report.count_styles,
        report.count_scripts,
    )
    return {"report_id": report_id, "status": AnalysisReport.Status.SUCCESS}


def _mark_failed(report_id: int, message: str) -> dict:
    """Flip the report to FAILED with a truncated error message."""
    AnalysisReport.objects.filter(pk=report_id).update(
        status=AnalysisReport.Status.FAILED,
        error_message=message[:1000],
    )
    return {"report_id": report_id, "status": AnalysisReport.Status.FAILED}
