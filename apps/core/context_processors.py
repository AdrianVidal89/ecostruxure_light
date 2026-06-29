"""
Template context processors for the core app.

``navigation`` injects the sidebar navigation model and app metadata into every
template, so the layout stays data-driven and easy to extend as new sections
come online in later build steps.
"""

from django.conf import settings


def navigation(request):
    """Provide sidebar nav items and global app metadata to all templates.

    Each nav item declares whether it is ``available`` yet. Items that point at
    URL patterns not yet wired (later build steps) render as disabled, so the
    intended information architecture is visible without breaking ``{% url %}``
    resolution.
    """
    user = getattr(request, "user", None)
    is_admin = bool(user and user.is_authenticated and getattr(user, "is_admin", False))

    nav_items = [
        {
            "label": "Dashboard",
            "url_name": "core:dashboard",
            "icon": "grid",
            "available": True,
        },
        {
            "label": "POCs",
            "url_name": "pocs:list",
            "icon": "folder",
            "available": True,
        },
        {
            "label": "Tasks",
            "url_name": "pocs:tasks",
            "icon": "list-checks",
            "available": True,
        },
        {
            "label": "Phase blueprint",
            "url_name": "pocs:phase_template_list",
            "icon": "git-branch",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Functional Analysis",
            "url_name": "pocs:fa_step_list",
            "icon": "clipboard-list",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Users",
            "url_name": "accounts:user_list",
            "icon": "users",
            "available": True,  # admin-only user management
            "admin_only": True,
        },
        {
            "label": "Branding",
            "url_name": "core:branding",
            "icon": "image",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Reports",
            "url_name": "reports:home",
            "icon": "file-text",
            "available": True,
        },
    ]

    # Hide admin-only entries from non-admins.
    visible = [i for i in nav_items if not i.get("admin_only") or is_admin]

    return {
        "app_name": "EcoStruxure Light",
        "app_version": getattr(settings, "APP_VERSION", "0.1.0"),
        "nav_items": visible,
        "branding": _branding(),
    }


def _branding():
    """The uploaded logo (or None). Guarded so it never breaks a request
    (e.g. before the table exists during migration)."""
    try:
        from .models import SiteBranding

        return SiteBranding.objects.first()
    except Exception:  # noqa: BLE001
        return None
