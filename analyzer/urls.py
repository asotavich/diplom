"""
analyzer/urls.py

API routes for the analyzer app. Mounted under /api/ by the project root
urls.py, so the full paths look like ``/api/auth/login/`` etc.
"""

from django.urls import path
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    AnalysisReportDetailView,
    AnalysisReportListCreateView,
    ProjectDetailView,
    ProjectListCreateView,
    RegisterView,
    ReportExportPdfView,
    ReportExportView,
    TaskStatusView,
    UserProfileView,
)

app_name = "analyzer"

urlpatterns = [
    # ---- Authentication -------------------------------------------------
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("auth/profile/", UserProfileView.as_view(), name="profile"),

    # ---- Projects -------------------------------------------------------
    path("projects/", ProjectListCreateView.as_view(), name="project_list"),
    path("projects/<int:pk>/", ProjectDetailView.as_view(), name="project_detail"),

    # ---- Reports --------------------------------------------------------
    path("reports/", AnalysisReportListCreateView.as_view(), name="report_list"),
    path("reports/<slug:slug>/", AnalysisReportDetailView.as_view(), name="report_detail"),
    path("reports/<slug:slug>/export/", ReportExportView.as_view(), name="report_export"),
    path("reports/<slug:slug>/export.pdf/", ReportExportPdfView.as_view(), name="report_export_pdf"),

    # ---- Task polling ---------------------------------------------------
    path("tasks/<str:task_id>/", TaskStatusView.as_view(), name="task_status"),
]
