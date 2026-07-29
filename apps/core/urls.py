from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("branding/", views.BrandingView.as_view(), name="branding"),
    path("db-backup/", views.DatabaseBackupView.as_view(), name="db_backup"),
    path("db-backup/download/", views.database_backup_download, name="db_backup_download"),
]
