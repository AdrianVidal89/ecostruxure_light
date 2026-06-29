from django.apps import AppConfig


class ReportsConfig(AppConfig):
    """Report templates (admin library) + report generation (phase & custom)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    label = "reports"
