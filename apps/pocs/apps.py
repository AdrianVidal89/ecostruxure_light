from django.apps import AppConfig


class PocsConfig(AppConfig):
    """POC domain: POC, POCMembership, Phase, Task, Test, AuditLog."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pocs"
    label = "pocs"

    def ready(self):
        # Register the audit-log signal handlers.
        from . import signals  # noqa: F401
