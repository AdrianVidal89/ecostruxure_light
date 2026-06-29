from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Custom user model, authentication, and (admin) user management UI."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
