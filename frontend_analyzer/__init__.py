"""
Loads the Celery app eagerly so @shared_task decorators registered in
sub-apps bind to the correct broker / result backend before any task is
enqueued.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
