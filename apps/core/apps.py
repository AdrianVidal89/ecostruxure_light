from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared utilities: base layout, context processors, mixins, templatetags."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
