"""
analyzer/admin.py

Django admin registrations for Project and AnalysisReport. Tuned so the
staff view is actually useful for debugging production traffic: list
columns, date hierarchy, search, and filters.
"""

from django.contrib import admin

from .models import AnalysisReport, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "report_count", "created_at")
    list_filter = ("created_at", "owner")
    search_fields = ("name", "description", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="reports", ordering="reports__count")
    def report_count(self, obj: Project) -> int:
        return obj.reports.count()


@admin.register(AnalysisReport)
class AnalysisReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "url",
        "created_by",
        "project",
        "status",
        "complexity_index",
        "scanned_at",
    )
    list_filter = ("status", "scanned_at", "project", "created_by")
    search_fields = (
        "url",
        "created_by__username",
        "project__name",
        "celery_task_id",
    )
    readonly_fields = (
        "scanned_at",
        "count_links",
        "count_styles",
        "count_scripts",
        "complexity_index",
        "raw_metadata",
        "celery_task_id",
        "error_message",
    )
    date_hierarchy = "scanned_at"
    autocomplete_fields = ("project", "created_by")
