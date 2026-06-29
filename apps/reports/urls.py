"""Reports URL configuration."""

from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    # Global Reports area (custom reports)
    path("", views.ReportsHomeView.as_view(), name="home"),
    path("generate/custom/", views.custom_generate, name="custom_generate"),
    # Report-type library (admin)
    path("types/", views.ReportTypeListView.as_view(), name="type_list"),
    path("types/create/", views.ReportTypeCreateView.as_view(), name="type_create"),
    path("types/<int:pk>/edit/", views.ReportTypeUpdateView.as_view(), name="type_edit"),
    path("types/<int:pk>/delete/", views.ReportTypeDeleteView.as_view(), name="type_delete"),
    # Global report settings (admin) — default template
    path("settings/", views.ReportSettingsView.as_view(), name="settings"),
    # Phase report generation (from a POC)
    path(
        "phase/<int:phase_pk>/generate/",
        views.phase_report_generate,
        name="phase_generate",
    ),
    path(
        "phase/<int:phase_pk>/generate-docs/",
        views.phase_documents_report,
        name="phase_documents",
    ),
    # Download
    path("<int:pk>/download/", views.report_download, name="download"),
]
